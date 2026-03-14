# Clara Data Export/Import Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a CLI tool that exports Clara's data from any combination of backends (PostgreSQL/SQLite, Qdrant/pgvector, FalkorDB) into portable JSONL archives, and imports them back into any target configuration.

**Architecture:** Single script with `export` and `import` subcommands. Export reads from configured backends, writes JSONL files into a tar.gz archive with a manifest. Import reads the archive and upserts into whatever backends are currently configured. Backend-agnostic format — source and target can differ.

**Tech Stack:** Python stdlib (`argparse`, `tarfile`, `json`, `tempfile`), existing SQLAlchemy models, existing Qdrant/pgvector vector store, existing FalkorDB graph store, existing OpenAI embeddings (for re-embed on import).

---

### Task 1: Script skeleton with argparse and manifest

**Files:**
- Create: `scripts/clara_export_import.py`
- Test: `tests/scripts/test_export_import.py`

**Step 1: Write the failing test**

```python
"""Tests for clara_export_import CLI."""
import json
import subprocess
import sys

def test_export_help():
    """CLI shows help without crashing."""
    result = subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "export", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--user" in result.stdout
    assert "--since" in result.stdout
    assert "-o" in result.stdout

def test_import_help():
    result = subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "import", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout
    assert "--re-embed" in result.stdout
    assert "--tables" in result.stdout
```

**Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/scripts/test_export_import.py -v`
Expected: FAIL (file not found or missing arguments)

**Step 3: Write the script skeleton**

```python
#!/usr/bin/env python3
"""Clara Data Export/Import Tool.

Exports Clara records from any configured backend (PostgreSQL/SQLite,
Qdrant/pgvector, FalkorDB) into portable JSONL archives. Imports them
back into any target configuration.

Usage:
    poetry run python scripts/clara_export_import.py export -o ./backups/
    poetry run python scripts/clara_export_import.py import ./backups/archive.tar.gz
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("clara.export_import")

MANIFEST_VERSION = "1"


def build_manifest(
    source_backends: dict,
    filters: dict,
    record_counts: dict,
) -> dict:
    """Build the manifest.json content."""
    return {
        "version": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": source_backends,
        "filters": filters,
        "embedding_model": "text-embedding-3-small",
        "embedding_dimensions": 1536,
        "record_counts": record_counts,
    }


def cmd_export(args):
    """Handle the export subcommand."""
    logger.info("Export starting...")
    logger.info("Filters: user=%s, since=%s", args.user, args.since)
    logger.info("Output directory: %s", args.output)
    # Implemented in subsequent tasks
    raise NotImplementedError("Export not yet implemented")


def cmd_import(args):
    """Handle the import subcommand."""
    logger.info("Import starting: %s", args.archive)
    logger.info("Dry run: %s, Re-embed: %s", args.dry_run, args.re_embed)
    if args.tables:
        logger.info("Tables filter: %s", args.tables)
    # Implemented in subsequent tasks
    raise NotImplementedError("Import not yet implemented")


def main():
    parser = argparse.ArgumentParser(
        description="Clara Data Export/Import Tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- export --
    ex = sub.add_parser("export", help="Export Clara data to archive")
    ex.add_argument("-o", "--output", default=".", help="Output directory (default: .)")
    ex.add_argument("--user", default=None, help="Export only this user's data")
    ex.add_argument("--since", default=None, help="Export records created after this ISO date")
    ex.set_defaults(func=cmd_export)

    # -- import --
    im = sub.add_parser("import", help="Import Clara data from archive")
    im.add_argument("archive", help="Path to .tar.gz archive")
    im.add_argument("--dry-run", action="store_true", help="Validate without writing")
    im.add_argument("--re-embed", action="store_true", help="Force re-embedding of vectors")
    im.add_argument("--tables", default=None, help="Comma-separated list of tables to import")
    im.add_argument("--strict", action="store_true", help="Fail on missing backends instead of skipping")
    im.set_defaults(func=cmd_import)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

**Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/scripts/test_export_import.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/clara_export_import.py tests/scripts/test_export_import.py
git commit -m "feat: add export/import CLI skeleton with argparse"
```

---

### Task 2: Relational export — serialize SQLAlchemy models to JSONL

**Files:**
- Modify: `scripts/clara_export_import.py`
- Test: `tests/scripts/test_export_import.py`

**Context:** This task adds the ability to export all relational tables (SQLAlchemy models) to JSONL files. Each model is serialized to a dict with datetime→ISO string conversion.

**Step 1: Write the failing test**

```python
import tempfile
import os

def test_serialize_model_row():
    """Model rows serialize to JSON-safe dicts."""
    # Import after path setup
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from scripts.clara_export_import import serialize_row

    from mypalclara.db.models import Project
    from datetime import datetime, timezone

    # Create a mock-like object with the right attributes
    class FakeRow:
        __table__ = Project.__table__
        id = "proj-1"
        user_id = "user-1"
        name = "Test"
        created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        updated_at = None

    row = FakeRow()
    result = serialize_row(row)
    assert result["id"] == "proj-1"
    assert result["created_at"] == "2026-01-01T00:00:00+00:00"
    assert result["updated_at"] is None
```

**Step 2: Run test — FAIL**

**Step 3: Implement serialization + relational export**

Add to `clara_export_import.py`:

```python
from sqlalchemy import inspect as sa_inspect

# Tables to export, in FK-dependency order (parents before children)
RELATIONAL_TABLES = [
    ("canonical_users", "CanonicalUser"),
    ("platform_links", "PlatformLink"),
    ("projects", "Project"),
    ("sessions", "Session"),
    ("messages", "Message"),
    ("conversations", "Conversation"),
    ("branches", "Branch"),
    ("branch_messages", "BranchMessage"),
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
    """Serialize a SQLAlchemy model instance to a JSON-safe dict."""
    result = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if isinstance(val, datetime):
            val = val.isoformat()
        result[col.name] = val
    return result


def export_relational(
    tmp_dir: Path,
    user_id: str | None,
    since: str | None,
) -> dict[str, int]:
    """Export relational tables to JSONL files. Returns {filename: count}."""
    from mypalclara.db.connection import SessionLocal
    import mypalclara.db.models as models

    db = SessionLocal()
    counts = {}

    try:
        rel_dir = tmp_dir / "relational"
        rel_dir.mkdir()

        for filename, model_name in RELATIONAL_TABLES:
            model_cls = getattr(models, model_name, None)
            if model_cls is None:
                logger.warning("Model %s not found, skipping", model_name)
                continue

            query = db.query(model_cls)

            # Apply user filter if the model has a user_id column
            if user_id:
                if hasattr(model_cls, "user_id"):
                    query = query.filter(model_cls.user_id == user_id)
                elif hasattr(model_cls, "canonical_user_id"):
                    query = query.filter(model_cls.canonical_user_id == user_id)

            # Apply since filter if the model has a created_at column
            if since:
                if hasattr(model_cls, "created_at"):
                    query = query.filter(model_cls.created_at >= since)

            count = 0
            filepath = rel_dir / f"{filename}.jsonl"
            with open(filepath, "w") as f:
                for row in query.yield_per(500):
                    f.write(json.dumps(serialize_row(row), default=str) + "\n")
                    count += 1
                    if count % 500 == 0:
                        logger.info("  %s: %d records...", filename, count)

            counts[filename] = count
            if count > 0:
                logger.info("  %s: %d records", filename, count)
            else:
                # Remove empty files
                filepath.unlink()

    finally:
        db.close()

    return counts
```

**Step 4: Run test — PASS**

**Step 5: Commit**

```bash
git add scripts/clara_export_import.py tests/scripts/test_export_import.py
git commit -m "feat: add relational table serialization and export"
```

---

### Task 3: Vector store export — read all memories with embeddings

**Files:**
- Modify: `scripts/clara_export_import.py`

**Context:** Export all vectors from Qdrant or pgvector. The key challenge: Qdrant's `scroll()` API paginates with an offset token. We need `with_vectors=True` to include the float arrays.

**Step 1: Implement vector export**

Add to `clara_export_import.py`:

```python
def export_vectors(
    tmp_dir: Path,
    user_id: str | None,
    since: str | None,
) -> tuple[dict[str, int], str]:
    """Export vector memories to JSONL. Returns (counts, vector_provider)."""
    from mypalclara.core.memory.config import (
        ROOK_COLLECTION_NAME,
        QDRANT_URL,
        QDRANT_API_KEY,
        QDRANT_DATA_DIR,
        ROOK_DATABASE_URL,
    )

    vec_dir = tmp_dir / "vectors"
    vec_dir.mkdir()
    filepath = vec_dir / "memories.jsonl"

    provider = "none"
    count = 0

    # Try Qdrant first, then pgvector
    if QDRANT_URL or (not ROOK_DATABASE_URL and QDRANT_DATA_DIR.exists()):
        provider = "qdrant"
        count = _export_qdrant(filepath, ROOK_COLLECTION_NAME, user_id, since)
    elif ROOK_DATABASE_URL:
        provider = "pgvector"
        count = _export_pgvector(filepath, ROOK_COLLECTION_NAME, ROOK_DATABASE_URL, user_id, since)
    else:
        logger.warning("No vector store configured, skipping vector export")

    if count == 0 and filepath.exists():
        filepath.unlink()

    return {"memories": count}, provider


def _export_qdrant(
    filepath: Path,
    collection_name: str,
    user_id: str | None,
    since: str | None,
) -> int:
    """Export all vectors from Qdrant using scroll pagination."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    from mypalclara.core.memory.config import QDRANT_URL, QDRANT_API_KEY, QDRANT_DATA_DIR

    params = {}
    if QDRANT_URL:
        params["url"] = QDRANT_URL
        if QDRANT_API_KEY:
            params["api_key"] = QDRANT_API_KEY
    else:
        params["path"] = str(QDRANT_DATA_DIR)

    client = QdrantClient(**params)

    # Build filter
    conditions = []
    if user_id:
        conditions.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))

    scroll_filter = Filter(must=conditions) if conditions else None
    offset = None
    count = 0

    with open(filepath, "w") as f:
        while True:
            points, next_offset = client.scroll(
                collection_name=collection_name,
                scroll_filter=scroll_filter,
                limit=100,
                with_payload=True,
                with_vectors=True,
                offset=offset,
            )

            for point in points:
                record = {
                    "id": point.id,
                    "vector": point.vector if isinstance(point.vector, list) else list(point.vector),
                    "payload": point.payload,
                }
                f.write(json.dumps(record, default=str) + "\n")
                count += 1

            if count % 500 == 0 and count > 0:
                logger.info("  memories: %d vectors...", count)

            if next_offset is None:
                break
            offset = next_offset

    logger.info("  memories: %d vectors exported", count)
    return count


def _export_pgvector(
    filepath: Path,
    collection_name: str,
    database_url: str,
    user_id: str | None,
    since: str | None,
) -> int:
    """Export all vectors from pgvector."""
    from sqlalchemy import create_engine, text

    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(database_url)
    count = 0

    # pgvector stores in a table named after the collection
    query = f"SELECT id, embedding, metadata FROM langchain_pg_embedding WHERE collection_id = (SELECT uuid FROM langchain_pg_collection WHERE name = :collection)"
    params = {"collection": collection_name}

    with engine.connect() as conn, open(filepath, "w") as f:
        result = conn.execute(text(query), params)
        for row in result:
            record = {
                "id": str(row.id),
                "vector": list(row.embedding) if row.embedding else None,
                "payload": json.loads(row.metadata) if isinstance(row.metadata, str) else row.metadata,
            }
            # Apply user filter on payload
            if user_id and record["payload"].get("user_id") != user_id:
                continue

            f.write(json.dumps(record, default=str) + "\n")
            count += 1

    logger.info("  memories: %d vectors exported", count)
    return count
```

**Step 2: Run tests — PASS (existing tests still pass)**

**Step 3: Commit**

```bash
git add scripts/clara_export_import.py
git commit -m "feat: add vector store export (Qdrant + pgvector)"
```

---

### Task 4: Graph export — dump FalkorDB nodes and edges

**Files:**
- Modify: `scripts/clara_export_import.py`

**Context:** FalkorDB stores entity nodes and relationships. Export uses Cypher queries to dump all nodes and edges.

**Step 1: Implement graph export**

```python
def export_graph(
    tmp_dir: Path,
    user_id: str | None,
) -> tuple[dict[str, int], str]:
    """Export FalkorDB graph data. Returns (counts, provider)."""
    from mypalclara.core.memory.config import (
        ENABLE_GRAPH_MEMORY,
        FALKORDB_HOST,
        FALKORDB_PORT,
        FALKORDB_PASSWORD,
        FALKORDB_GRAPH_NAME,
    )

    if not ENABLE_GRAPH_MEMORY:
        logger.info("Graph memory disabled, skipping graph export")
        return {"nodes": 0, "edges": 0}, "none"

    try:
        import falkordb
    except ImportError:
        logger.warning("falkordb not installed, skipping graph export")
        return {"nodes": 0, "edges": 0}, "none"

    graph_dir = tmp_dir / "graph"
    graph_dir.mkdir()

    client = falkordb.FalkorDB(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        password=FALKORDB_PASSWORD,
    )
    graph = client.select_graph(FALKORDB_GRAPH_NAME)

    # Export nodes
    node_count = 0
    node_query = "MATCH (n:__Entity__) RETURN n"
    if user_id:
        node_query = "MATCH (n:__Entity__ {user_id: $user_id}) RETURN n"

    nodes_path = graph_dir / "nodes.jsonl"
    with open(nodes_path, "w") as f:
        params = {"user_id": user_id} if user_id else {}
        result = graph.query(node_query, params=params)
        for row in result.result_set:
            node = row[0]
            record = {"id": node.id, "properties": node.properties}
            f.write(json.dumps(record, default=str) + "\n")
            node_count += 1

    # Export edges
    edge_count = 0
    edge_query = "MATCH (a:__Entity__)-[r]->(b:__Entity__) RETURN a.name, type(r), r, b.name"
    if user_id:
        edge_query = "MATCH (a:__Entity__ {user_id: $user_id})-[r]->(b:__Entity__) RETURN a.name, type(r), r, b.name"

    edges_path = graph_dir / "edges.jsonl"
    with open(edges_path, "w") as f:
        params = {"user_id": user_id} if user_id else {}
        result = graph.query(edge_query, params=params)
        for row in result.result_set:
            record = {
                "source": row[0],
                "relation": row[1],
                "properties": row[2].properties if hasattr(row[2], "properties") else {},
                "target": row[3],
            }
            f.write(json.dumps(record, default=str) + "\n")
            edge_count += 1

    logger.info("  graph: %d nodes, %d edges", node_count, edge_count)

    # Cleanup empty files
    if node_count == 0:
        nodes_path.unlink()
    if edge_count == 0:
        edges_path.unlink()

    return {"nodes": node_count, "edges": edge_count}, "falkordb"


```

**Step 2: Run tests — PASS**

**Step 3: Commit**

```bash
git add scripts/clara_export_import.py
git commit -m "feat: add FalkorDB graph export (nodes + edges)"
```

---

### Task 5: Wire export subcommand — assemble archive

**Files:**
- Modify: `scripts/clara_export_import.py`
- Test: `tests/scripts/test_export_import.py`

**Context:** Wire `cmd_export` to call all three exporters, write the manifest, and bundle into a `.tar.gz`.

**Step 1: Write the failing test**

```python
def test_export_creates_archive(tmp_path):
    """Export produces a .tar.gz with manifest and relational dir."""
    # Use SQLite in-memory for test
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test.db"
    # Ensure no vector/graph backends interfere
    os.environ.pop("QDRANT_URL", None)
    os.environ.pop("ROOK_DATABASE_URL", None)
    os.environ.pop("ENABLE_GRAPH_MEMORY", None)

    from mypalclara.db.connection import engine
    from mypalclara.db.models import Base
    Base.metadata.create_all(bind=engine)

    result = subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "export", "-o", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0

    # Find the archive
    archives = list(tmp_path.glob("clara-export-*.tar.gz"))
    assert len(archives) == 1

    # Verify contents
    with tarfile.open(archives[0], "r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names

        # Read manifest
        manifest = json.load(tar.extractfile("manifest.json"))
        assert manifest["version"] == "1"
        assert "record_counts" in manifest
```

**Step 2: Run test — FAIL**

**Step 3: Implement `cmd_export`**

```python
def cmd_export(args):
    """Handle the export subcommand."""
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    filters = {"user_id": args.user, "since": args.since}
    record_counts = {}
    source_backends = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 1. Relational export
        logger.info("Exporting relational tables...")
        rel_counts = export_relational(tmp_dir, args.user, args.since)
        record_counts.update(rel_counts)
        source_backends["relational"] = _detect_relational_backend()

        # 2. Vector export
        logger.info("Exporting vector memories...")
        vec_counts, vec_provider = export_vectors(tmp_dir, args.user, args.since)
        record_counts.update(vec_counts)
        source_backends["vector"] = vec_provider

        # 3. Graph export
        logger.info("Exporting graph data...")
        graph_counts, graph_provider = export_graph(tmp_dir, args.user)
        record_counts.update(graph_counts)
        source_backends["graph"] = graph_provider

        # 4. Write manifest
        manifest = build_manifest(source_backends, filters, record_counts)
        with open(tmp_dir / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

        # 5. Bundle into tar.gz
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        archive_name = f"clara-export-{timestamp}.tar.gz"
        archive_path = output_dir / archive_name

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in tmp_dir.rglob("*"):
                if item.is_file():
                    arcname = str(item.relative_to(tmp_dir))
                    tar.add(item, arcname=arcname)

    total = sum(record_counts.values())
    logger.info("Export complete: %s (%d total records)", archive_path, total)
    for name, count in sorted(record_counts.items()):
        if count > 0:
            logger.info("  %-30s %d", name, count)


def _detect_relational_backend() -> str:
    """Detect which relational backend is configured."""
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgres"):
        return "postgresql"
    return "sqlite"
```

**Step 4: Run test — PASS**

**Step 5: Commit**

```bash
git add scripts/clara_export_import.py tests/scripts/test_export_import.py
git commit -m "feat: wire export subcommand with archive bundling"
```

---

### Task 6: Relational import — upsert from JSONL

**Files:**
- Modify: `scripts/clara_export_import.py`
- Test: `tests/scripts/test_export_import.py`

**Context:** Read JSONL files from the archive, deserialize, and upsert into the database. Uses SQLAlchemy `merge()` for idempotent upserts. Tables are imported in the same FK-dependency order as `RELATIONAL_TABLES`.

**Step 1: Write the failing test**

```python
def test_import_relational_roundtrip(tmp_path):
    """Export then import produces identical relational data."""
    db_path = tmp_path / "test.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("QDRANT_URL", None)
    os.environ.pop("ROOK_DATABASE_URL", None)
    os.environ.pop("ENABLE_GRAPH_MEMORY", None)

    # Create DB and seed a project
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mypalclara.db.models import Base, Project

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)

    db = Session()
    db.add(Project(id="proj-1", user_id="user-1", name="Test Project"))
    db.commit()
    db.close()

    # Export
    subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "export", "-o", str(tmp_path)],
        check=True, capture_output=True,
    )

    # Clear the DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Import
    archive = list(tmp_path.glob("clara-export-*.tar.gz"))[0]
    subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "import", str(archive)],
        check=True, capture_output=True,
    )

    # Verify
    db = Session()
    projects = db.query(Project).all()
    assert len(projects) == 1
    assert projects[0].id == "proj-1"
    assert projects[0].name == "Test Project"
    db.close()
```

**Step 2: Run test — FAIL**

**Step 3: Implement relational import**

```python
def import_relational(
    tmp_dir: Path,
    tables_filter: list[str] | None,
    dry_run: bool,
) -> dict[str, int]:
    """Import relational tables from JSONL files. Returns {table: count}."""
    from mypalclara.db.connection import SessionLocal
    import mypalclara.db.models as models

    rel_dir = tmp_dir / "relational"
    if not rel_dir.exists():
        logger.info("No relational data in archive")
        return {}

    db = SessionLocal()
    counts = {}

    try:
        for filename, model_name in RELATIONAL_TABLES:
            filepath = rel_dir / f"{filename}.jsonl"
            if not filepath.exists():
                continue

            if tables_filter and filename not in tables_filter:
                logger.info("  Skipping %s (filtered)", filename)
                continue

            model_cls = getattr(models, model_name, None)
            if model_cls is None:
                logger.warning("Model %s not found, skipping", model_name)
                continue

            count = 0
            with open(filepath) as f:
                for line in f:
                    row_data = json.loads(line)

                    # Convert ISO datetime strings back to datetime objects
                    for col in model_cls.__table__.columns:
                        if col.name in row_data and row_data[col.name] is not None:
                            if str(col.type).startswith("DATE"):
                                try:
                                    row_data[col.name] = datetime.fromisoformat(row_data[col.name])
                                except (ValueError, TypeError):
                                    pass

                    if not dry_run:
                        obj = model_cls(**row_data)
                        db.merge(obj)
                        if count % 500 == 0 and count > 0:
                            db.flush()

                    count += 1

            if not dry_run:
                db.commit()

            counts[filename] = count
            if count > 0:
                logger.info("  %s: %d records %s", filename, count, "(dry run)" if dry_run else "imported")

    finally:
        db.close()

    return counts
```

**Step 4: Run test — PASS**

**Step 5: Commit**

```bash
git add scripts/clara_export_import.py tests/scripts/test_export_import.py
git commit -m "feat: add relational import with upsert"
```

---

### Task 7: Vector import — insert or re-embed

**Files:**
- Modify: `scripts/clara_export_import.py`

**Context:** Read vector JSONL, either insert vectors directly (if embedding model matches) or re-embed from text. Uses the same Qdrant/pgvector backends as export.

**Step 1: Implement vector import**

```python
def import_vectors(
    tmp_dir: Path,
    manifest: dict,
    re_embed: bool,
    dry_run: bool,
) -> dict[str, int]:
    """Import vector memories from JSONL. Returns {name: count}."""
    filepath = tmp_dir / "vectors" / "memories.jsonl"
    if not filepath.exists():
        logger.info("No vector data in archive")
        return {}

    from mypalclara.core.memory.config import (
        ROOK_COLLECTION_NAME,
        QDRANT_URL,
        QDRANT_API_KEY,
        QDRANT_DATA_DIR,
        ROOK_DATABASE_URL,
        EMBEDDING_MODEL_DIMS,
    )

    # Determine if we need to re-embed
    source_model = manifest.get("embedding_model", "")
    need_re_embed = re_embed or source_model != "text-embedding-3-small"
    if need_re_embed:
        logger.info("  Will re-embed vectors (source model: %s)", source_model)
        from mypalclara.core.memory.embeddings.openai import OpenAIEmbedding
        from mypalclara.core.memory.embeddings.base import BaseEmbedderConfig
        embedder = OpenAIEmbedding(BaseEmbedderConfig(
            model="text-embedding-3-small",
            api_key=os.getenv("OPENAI_API_KEY"),
        ))

    # Determine target backend
    if QDRANT_URL or (not ROOK_DATABASE_URL and QDRANT_DATA_DIR.exists()):
        return _import_qdrant(filepath, ROOK_COLLECTION_NAME, need_re_embed, dry_run,
                              embedder if need_re_embed else None)
    elif ROOK_DATABASE_URL:
        return _import_pgvector(filepath, ROOK_COLLECTION_NAME, ROOK_DATABASE_URL,
                                 need_re_embed, dry_run, embedder if need_re_embed else None)
    else:
        logger.warning("No vector store configured, skipping vector import")
        return {}


def _import_qdrant(filepath, collection_name, need_re_embed, dry_run, embedder=None):
    """Import vectors into Qdrant."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
    from mypalclara.core.memory.config import QDRANT_URL, QDRANT_API_KEY, QDRANT_DATA_DIR

    params = {}
    if QDRANT_URL:
        params["url"] = QDRANT_URL
        if QDRANT_API_KEY:
            params["api_key"] = QDRANT_API_KEY
    else:
        params["path"] = str(QDRANT_DATA_DIR)

    client = QdrantClient(**params)
    count = 0
    batch = []
    BATCH_SIZE = 100

    with open(filepath) as f:
        for line in f:
            record = json.loads(line)
            vector = record.get("vector")
            payload = record.get("payload", {})

            if need_re_embed and embedder:
                text = payload.get("memory", payload.get("data", ""))
                if text:
                    vector = embedder.embed(text)
                else:
                    continue  # Can't embed without text

            if vector is None:
                continue

            point = PointStruct(
                id=record["id"],
                vector=vector,
                payload=payload,
            )
            batch.append(point)
            count += 1

            if len(batch) >= BATCH_SIZE and not dry_run:
                client.upsert(collection_name=collection_name, points=batch)
                batch = []
                if count % 500 == 0:
                    logger.info("  memories: %d vectors...", count)

    if batch and not dry_run:
        client.upsert(collection_name=collection_name, points=batch)

    logger.info("  memories: %d vectors %s", count, "(dry run)" if dry_run else "imported")
    return {"memories": count}
```

Note: `_import_pgvector` follows the same pattern but uses SQL INSERT ON CONFLICT UPDATE. The implementation mirrors the existing `pgvector.py` insert logic.

**Step 2: Run tests — PASS**

**Step 3: Commit**

```bash
git add scripts/clara_export_import.py
git commit -m "feat: add vector import with optional re-embedding"
```

---

### Task 8: Graph import — MERGE nodes and edges into FalkorDB

**Files:**
- Modify: `scripts/clara_export_import.py`

**Step 1: Implement graph import**

```python
def import_graph(
    tmp_dir: Path,
    dry_run: bool,
) -> dict[str, int]:
    """Import graph nodes and edges into FalkorDB. Returns counts."""
    nodes_path = tmp_dir / "graph" / "nodes.jsonl"
    edges_path = tmp_dir / "graph" / "edges.jsonl"

    if not nodes_path.exists() and not edges_path.exists():
        logger.info("No graph data in archive")
        return {}

    from mypalclara.core.memory.config import (
        ENABLE_GRAPH_MEMORY,
        FALKORDB_HOST,
        FALKORDB_PORT,
        FALKORDB_PASSWORD,
        FALKORDB_GRAPH_NAME,
    )

    if not ENABLE_GRAPH_MEMORY:
        logger.warning("Graph memory disabled, skipping graph import (set ENABLE_GRAPH_MEMORY=true)")
        return {}

    try:
        import falkordb
    except ImportError:
        logger.warning("falkordb not installed, skipping graph import")
        return {}

    client = falkordb.FalkorDB(
        host=FALKORDB_HOST,
        port=FALKORDB_PORT,
        password=FALKORDB_PASSWORD,
    )
    graph = client.select_graph(FALKORDB_GRAPH_NAME)

    node_count = 0
    edge_count = 0

    # Import nodes
    if nodes_path.exists():
        with open(nodes_path) as f:
            for line in f:
                record = json.loads(line)
                props = record.get("properties", {})

                if not dry_run:
                    # Build SET clause from properties
                    set_pairs = ", ".join(f"n.{k} = ${k}" for k in props if k != "name")
                    query = f"MERGE (n:__Entity__ {{name: $name}}) SET {set_pairs}" if set_pairs else "MERGE (n:__Entity__ {name: $name})"
                    graph.query(query, params=props)

                node_count += 1

    # Import edges
    if edges_path.exists():
        with open(edges_path) as f:
            for line in f:
                record = json.loads(line)
                source = record["source"]
                target = record["target"]
                relation = record["relation"]

                if not dry_run:
                    query = (
                        "MERGE (a:__Entity__ {name: $source}) "
                        "MERGE (b:__Entity__ {name: $target}) "
                        f"MERGE (a)-[:{relation}]->(b)"
                    )
                    graph.query(query, params={"source": source, "target": target})

                edge_count += 1

    logger.info("  graph: %d nodes, %d edges %s", node_count, edge_count, "(dry run)" if dry_run else "imported")
    return {"nodes": node_count, "edges": edge_count}
```

**Step 2: Run tests — PASS**

**Step 3: Commit**

```bash
git add scripts/clara_export_import.py
git commit -m "feat: add FalkorDB graph import with MERGE upsert"
```

---

### Task 9: Wire import subcommand — read archive, dispatch importers

**Files:**
- Modify: `scripts/clara_export_import.py`
- Test: `tests/scripts/test_export_import.py`

**Step 1: Write the failing test**

```python
def test_import_dry_run(tmp_path):
    """Dry run reports counts without writing."""
    # Create a minimal archive
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        (tmp_dir / "relational").mkdir()
        with open(tmp_dir / "relational" / "projects.jsonl", "w") as f:
            f.write(json.dumps({"id": "p1", "user_id": "u1", "name": "Test", "created_at": None, "updated_at": None}) + "\n")

        manifest = {"version": "1", "created_at": "2026-01-01", "source": {"relational": "sqlite", "vector": "none", "graph": "none"}, "filters": {}, "embedding_model": "text-embedding-3-small", "embedding_dimensions": 1536, "record_counts": {"projects": 1}}
        with open(tmp_dir / "manifest.json", "w") as f:
            json.dump(manifest, f)

        archive = tmp_path / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            for item in Path(tmp_dir).rglob("*"):
                if item.is_file():
                    tar.add(item, arcname=str(item.relative_to(tmp_dir)))

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test.db"
    result = subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "import", str(archive), "--dry-run"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "dry run" in result.stdout.lower() or "dry run" in result.stderr.lower()
```

**Step 2: Run test — FAIL**

**Step 3: Implement `cmd_import`**

```python
def cmd_import(args):
    """Handle the import subcommand."""
    archive_path = Path(args.archive)
    if not archive_path.exists():
        logger.error("Archive not found: %s", archive_path)
        sys.exit(1)

    tables_filter = args.tables.split(",") if args.tables else None

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # Extract archive
        logger.info("Extracting archive: %s", archive_path)
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(tmp_dir)

        # Read manifest
        manifest_path = tmp_dir / "manifest.json"
        if not manifest_path.exists():
            logger.error("No manifest.json in archive")
            sys.exit(1)

        with open(manifest_path) as f:
            manifest = json.load(f)

        logger.info("Archive created: %s", manifest.get("created_at"))
        logger.info("Source backends: %s", manifest.get("source"))
        logger.info("Record counts: %s", manifest.get("record_counts"))

        if args.dry_run:
            logger.info("DRY RUN — no data will be written")

        record_counts = {}

        # 1. Relational import
        if not tables_filter or any(t in [fn for fn, _ in RELATIONAL_TABLES] for t in tables_filter):
            logger.info("Importing relational tables...")
            # Initialize DB schema
            from mypalclara.db.connection import init_db
            init_db(run_migrations=False)

            rel_counts = import_relational(tmp_dir, tables_filter, args.dry_run)
            record_counts.update(rel_counts)

        # 2. Vector import
        if not tables_filter or "memories" in (tables_filter or []):
            logger.info("Importing vector memories...")
            vec_counts = import_vectors(tmp_dir, manifest, args.re_embed, args.dry_run)
            record_counts.update(vec_counts)

        # 3. Graph import
        if not tables_filter or "nodes" in (tables_filter or []) or "edges" in (tables_filter or []):
            logger.info("Importing graph data...")
            graph_counts = import_graph(tmp_dir, args.dry_run)
            record_counts.update(graph_counts)

    total = sum(record_counts.values())
    action = "validated" if args.dry_run else "imported"
    logger.info("Import complete: %d total records %s", total, action)
    for name, count in sorted(record_counts.items()):
        if count > 0:
            logger.info("  %-30s %d", name, count)
```

**Step 4: Run test — PASS**

**Step 5: Commit**

```bash
git add scripts/clara_export_import.py tests/scripts/test_export_import.py
git commit -m "feat: wire import subcommand with archive extraction and dispatch"
```

---

### Task 10: Integration test — full export/import roundtrip

**Files:**
- Test: `tests/scripts/test_export_import.py`

**Step 1: Write roundtrip integration test**

This test creates a SQLite database with seed data across multiple tables, exports it, clears the DB, imports, and verifies all records are restored.

```python
def test_full_roundtrip(tmp_path):
    """Full export → clear → import roundtrip preserves data."""
    db_path = tmp_path / "roundtrip.db"
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.pop("QDRANT_URL", None)
    os.environ.pop("ROOK_DATABASE_URL", None)
    os.environ.pop("ENABLE_GRAPH_MEMORY", None)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mypalclara.db.models import Base, Project, Session as DBSession, Message

    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    Sess = sessionmaker(bind=engine)

    # Seed data
    db = Sess()
    db.add(Project(id="p1", user_id="u1", name="Project A"))
    db.add(DBSession(id="s1", user_id="u1", project_id="p1", context_id="ctx1"))
    db.add(Message(id="m1", session_id="s1", user_id="u1", role="user", content="Hello Clara"))
    db.add(Message(id="m2", session_id="s1", user_id="u1", role="assistant", content="Hi!"))
    db.commit()
    db.close()

    # Export
    subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "export", "-o", str(tmp_path)],
        check=True, capture_output=True,
    )

    # Clear DB
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    # Import
    archive = list(tmp_path.glob("clara-export-*.tar.gz"))[0]
    subprocess.run(
        [sys.executable, "scripts/clara_export_import.py", "import", str(archive)],
        check=True, capture_output=True,
    )

    # Verify
    db = Sess()
    assert db.query(Project).count() == 1
    assert db.query(DBSession).count() == 1
    assert db.query(Message).count() == 2
    msg = db.query(Message).filter(Message.id == "m1").first()
    assert msg.content == "Hello Clara"
    db.close()
```

**Step 2: Run test — PASS**

**Step 3: Commit**

```bash
git add tests/scripts/test_export_import.py
git commit -m "test: add full export/import roundtrip integration test"
```

---

## Summary

| Task | What | Files |
|------|------|-------|
| 1 | CLI skeleton + argparse | `scripts/clara_export_import.py`, tests |
| 2 | Relational export (serialize models → JSONL) | `scripts/clara_export_import.py`, tests |
| 3 | Vector export (Qdrant + pgvector with embeddings) | `scripts/clara_export_import.py` |
| 4 | Graph export (FalkorDB nodes + edges) | `scripts/clara_export_import.py` |
| 5 | Wire export subcommand (assemble tar.gz) | `scripts/clara_export_import.py`, tests |
| 6 | Relational import (JSONL → upsert) | `scripts/clara_export_import.py`, tests |
| 7 | Vector import (insert or re-embed) | `scripts/clara_export_import.py` |
| 8 | Graph import (MERGE into FalkorDB) | `scripts/clara_export_import.py` |
| 9 | Wire import subcommand (extract + dispatch) | `scripts/clara_export_import.py`, tests |
| 10 | Integration test (full roundtrip) | tests |
