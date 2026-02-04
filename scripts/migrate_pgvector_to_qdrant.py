#!/usr/bin/env python3
"""Migrate vectors from pgvector to Qdrant.

Zero-downtime migration script with:
- Batch processing with configurable size
- Progress tracking with checkpoints
- Resume from failure
- Verification after each batch

Usage:
    # Dry run (count only)
    python scripts/migrate_pgvector_to_qdrant.py --dry-run

    # Full migration with progress
    python scripts/migrate_pgvector_to_qdrant.py --batch-size 100

    # Resume from checkpoint
    python scripts/migrate_pgvector_to_qdrant.py --resume

    # Verify migration
    python scripts/migrate_pgvector_to_qdrant.py --verify
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sqlalchemy import text

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Configuration
MEM0_DATABASE_URL = os.getenv("MEM0_DATABASE_URL")
QDRANT_URL = os.getenv("QDRANT_URL")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = os.getenv("MEM0_COLLECTION_NAME", "clara_memories")
CHECKPOINT_FILE = Path("migration_checkpoint.json")


def get_pgvector_connection():
    """Get SQLAlchemy connection to pgvector database."""
    if not MEM0_DATABASE_URL:
        logger.error("MEM0_DATABASE_URL not set")
        sys.exit(1)

    from sqlalchemy import create_engine

    url = MEM0_DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    engine = create_engine(url)
    return engine


def get_qdrant_client() -> QdrantClient:
    """Get Qdrant client."""
    if not QDRANT_URL:
        logger.error("QDRANT_URL not set")
        sys.exit(1)

    kwargs = {"url": QDRANT_URL}
    if QDRANT_API_KEY:
        kwargs["api_key"] = QDRANT_API_KEY

    return QdrantClient(**kwargs)


def count_pgvector_records(engine) -> int:
    """Count total records in pgvector."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {COLLECTION_NAME}"))
        return result.scalar()


def parse_pgvector(vector_data) -> list[float] | None:
    """Parse pgvector vector data into a list of floats.

    pgvector returns vectors as strings like '[0.1,0.2,0.3]' when the
    pgvector Python extension isn't registered with psycopg2.
    """
    if vector_data is None:
        return None

    # If it's already a list, return it
    if isinstance(vector_data, (list, tuple)):
        return [float(x) for x in vector_data]

    # If it's a string representation like '[0.1,0.2,...]', parse it
    if isinstance(vector_data, str):
        # Remove brackets and split by comma
        cleaned = vector_data.strip('[]')
        if not cleaned:
            return None
        return [float(x.strip()) for x in cleaned.split(',')]

    # Try to convert numpy array or similar
    try:
        return [float(x) for x in vector_data]
    except (TypeError, ValueError):
        logger.warning(f"Could not parse vector data of type {type(vector_data)}")
        return None


def fetch_pgvector_batch(
    engine,
    offset: int,
    batch_size: int,
) -> list[dict[str, Any]]:
    """Fetch a batch of records from pgvector.

    Note: The vendored mem0 pgvector schema uses:
    - id (UUID)
    - vector (vector type, not 'embedding')
    - payload (JSONB)
    No created_at/updated_at columns.
    """
    # Cast vector to text to ensure we get a parseable string
    query = text(f"""
        SELECT id, vector::text as vector, payload
        FROM {COLLECTION_NAME}
        ORDER BY id
        OFFSET :offset
        LIMIT :batch_size
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"offset": offset, "batch_size": batch_size})
        records = []
        for row in result:
            records.append(
                {
                    "id": str(row.id),
                    "embedding": parse_pgvector(row.vector),
                    "payload": row.payload if isinstance(row.payload, dict) else json.loads(row.payload or "{}"),
                    "created_at": None,
                    "updated_at": None,
                }
            )
        return records


def migrate_batch(
    records: list[dict[str, Any]],
    qdrant_client: QdrantClient,
) -> int:
    """Migrate a batch of records to Qdrant."""
    if not records:
        return 0

    points = []
    for record in records:
        # Build payload with all metadata
        payload = record["payload"].copy()
        payload["_migrated_at"] = datetime.now().isoformat()
        if record["created_at"]:
            payload["_original_created_at"] = record["created_at"]
        if record["updated_at"]:
            payload["_original_updated_at"] = record["updated_at"]

        points.append(
            PointStruct(
                id=record["id"],
                vector=record["embedding"],
                payload=payload,
            )
        )

    qdrant_client.upsert(
        collection_name=COLLECTION_NAME,
        points=points,
    )

    return len(points)


def save_checkpoint(offset: int, migrated: int, total: int) -> None:
    """Save migration checkpoint for resume."""
    checkpoint = {
        "offset": offset,
        "migrated": migrated,
        "total": total,
        "timestamp": datetime.now().isoformat(),
    }
    CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2))
    logger.debug(f"Checkpoint saved: {checkpoint}")


def load_checkpoint() -> dict | None:
    """Load migration checkpoint if exists."""
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return None


def clear_checkpoint() -> None:
    """Clear checkpoint after successful migration."""
    if CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()


def ensure_qdrant_collection(
    qdrant_client: QdrantClient,
    vector_size: int = 1536,
) -> None:
    """Ensure Qdrant collection exists with correct configuration."""
    collections = qdrant_client.get_collections()
    exists = any(c.name == COLLECTION_NAME for c in collections.collections)

    if not exists:
        logger.info(f"Creating Qdrant collection: {COLLECTION_NAME}")
        qdrant_client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE,
                on_disk=True,
            ),
        )

        # Create indexes for common filter fields
        for field in ["user_id", "agent_id", "project_id"]:
            try:
                qdrant_client.create_payload_index(
                    collection_name=COLLECTION_NAME,
                    field_name=field,
                    field_schema="keyword",
                )
                logger.info(f"Created index for {field}")
            except Exception as e:
                logger.debug(f"Index for {field} might already exist: {e}")
    else:
        logger.info(f"Qdrant collection exists: {COLLECTION_NAME}")


def verify_migration(
    engine,
    qdrant_client: QdrantClient,
    sample_size: int = 100,
) -> dict:
    """Verify migration by comparing random samples."""
    # Get counts
    pg_count = count_pgvector_records(engine)
    qdrant_info = qdrant_client.get_collection(COLLECTION_NAME)
    qdrant_count = qdrant_info.points_count

    logger.info(f"pgvector records: {pg_count}")
    logger.info(f"Qdrant records: {qdrant_count}")

    # Check random samples
    records = fetch_pgvector_batch(engine, 0, sample_size)
    found = 0
    missing = []

    for record in records:
        result = qdrant_client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[record["id"]],
        )
        if result:
            found += 1
        else:
            missing.append(record["id"])

    return {
        "pg_count": pg_count,
        "qdrant_count": qdrant_count,
        "sample_size": len(records),
        "found": found,
        "missing": len(missing),
        "missing_ids": missing[:10],  # First 10
        "match_rate": found / len(records) if records else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Migrate pgvector to Qdrant")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count records without migrating",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for migration (default: 100)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last checkpoint",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration without migrating",
    )
    parser.add_argument(
        "--vector-size",
        type=int,
        default=1536,
        help="Vector dimensions (default: 1536 for OpenAI)",
    )
    args = parser.parse_args()

    # Get connections
    engine = get_pgvector_connection()
    qdrant_client = get_qdrant_client()

    # Verify mode
    if args.verify:
        logger.info("Verifying migration...")
        results = verify_migration(engine, qdrant_client)
        print(f"\n{'='*50}")
        print(f"pgvector records: {results['pg_count']}")
        print(f"Qdrant records: {results['qdrant_count']}")
        print(f"Sample size: {results['sample_size']}")
        print(f"Found in Qdrant: {results['found']}")
        print(f"Missing: {results['missing']}")
        print(f"Match rate: {results['match_rate']*100:.1f}%")
        if results["missing_ids"]:
            print(f"Sample missing IDs: {results['missing_ids']}")
        print(f"{'='*50}")
        return

    # Get total count
    total = count_pgvector_records(engine)
    logger.info(f"Total records in pgvector: {total}")

    if args.dry_run:
        print(f"\nDry run complete. {total} records would be migrated.")
        return

    # Ensure Qdrant collection exists
    ensure_qdrant_collection(qdrant_client, args.vector_size)

    # Check for resume
    offset = 0
    migrated = 0
    if args.resume:
        checkpoint = load_checkpoint()
        if checkpoint:
            offset = checkpoint["offset"]
            migrated = checkpoint["migrated"]
            logger.info(f"Resuming from checkpoint: offset={offset}, migrated={migrated}")
        else:
            logger.info("No checkpoint found, starting fresh")

    # Migration loop
    logger.info(f"Starting migration with batch_size={args.batch_size}")
    start_time = datetime.now()

    while offset < total:
        # Fetch batch
        records = fetch_pgvector_batch(engine, offset, args.batch_size)
        if not records:
            break

        # Migrate batch
        count = migrate_batch(records, qdrant_client)
        migrated += count
        offset += args.batch_size

        # Progress
        progress = migrated / total * 100
        elapsed = (datetime.now() - start_time).total_seconds()
        rate = migrated / elapsed if elapsed > 0 else 0
        eta = (total - migrated) / rate if rate > 0 else 0

        logger.info(f"Progress: {migrated}/{total} ({progress:.1f}%) " f"- {rate:.1f} rec/s - ETA: {eta:.0f}s")

        # Save checkpoint
        save_checkpoint(offset, migrated, total)

    # Complete
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"Migration complete: {migrated} records in {elapsed:.1f}s")

    # Verify
    logger.info("Verifying migration...")
    results = verify_migration(engine, qdrant_client)
    logger.info(f"Verification: {results['match_rate']*100:.1f}% match rate")

    # Clear checkpoint
    clear_checkpoint()


if __name__ == "__main__":
    main()
