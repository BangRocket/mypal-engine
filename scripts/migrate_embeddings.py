#!/usr/bin/env python3
"""Re-embed all memories after switching embedding model.

Reads memory text from the memory_history database table (which survives
vector store changes), re-embeds with the currently configured provider,
and inserts into the vector store.

Usage:
    # Dry run (count memories, show config)
    python scripts/migrate_embeddings.py --dry-run

    # Full migration
    python scripts/migrate_embeddings.py

    # With custom batch size
    python scripts/migrate_embeddings.py --batch-size 50
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fetch_memories_from_db() -> list[dict]:
    """Fetch the latest version of each memory from memory_history table.

    Returns list of dicts with keys: memory_id, memory (text), event, actor_id, role.
    Only returns the most recent non-deleted version of each memory.
    """
    from sqlalchemy import text as sql_text

    from mypalclara.db import SessionLocal

    session = SessionLocal()
    try:
        # Get the latest event per memory_id, excluding deleted ones
        rows = session.execute(
            sql_text("""
                SELECT DISTINCT ON (memory_id)
                    memory_id, new_memory, event, actor_id, role, created_at
                FROM memory_history
                WHERE is_deleted = false
                  AND new_memory IS NOT NULL
                  AND new_memory != ''
                  AND event IN ('ADD', 'UPDATE')
                ORDER BY memory_id, created_at DESC
            """)
        ).fetchall()

        memories = []
        for row in rows:
            memories.append({
                "id": row.memory_id,
                "memory": row.new_memory,
                "actor_id": row.actor_id,
                "role": row.role,
            })
        return memories
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Re-embed memories after embedding model change")
    parser.add_argument("--dry-run", action="store_true", help="Count memories and show config without migrating")
    parser.add_argument("--batch-size", type=int, default=25, help="Memories per batch (default: 25)")
    args = parser.parse_args()

    from mypalclara.core.memory.config import (
        EMBEDDING_MODEL_DIMS,
        EMBEDDING_PROVIDER,
        PALACE,
        config,
    )

    embedder_config = config.get("embedder", {})
    model = embedder_config.get("config", {}).get("model", "unknown")

    logger.info(f"Embedding provider: {EMBEDDING_PROVIDER}")
    logger.info(f"Embedding model: {model}")
    logger.info(f"Embedding dimensions: {EMBEDDING_MODEL_DIMS}")

    if PALACE is None:
        logger.error("Palace not initialized — check your configuration")
        sys.exit(1)

    # Fetch memories from the database (survives vector store changes)
    logger.info("Fetching memories from database (memory_history)...")
    memories = _fetch_memories_from_db()
    total = len(memories)
    logger.info(f"Found {total} memories to re-embed")

    if args.dry_run:
        logger.info("Dry run — no changes made")
        if memories:
            logger.info(f"Sample: {memories[0]['memory'][:100]}...")
        return

    if total == 0:
        logger.info("No memories to migrate")
        return

    # Recreate vector store collection with correct dimensions
    vs = PALACE.vector_store
    logger.info(f"Recreating Qdrant collection with {EMBEDDING_MODEL_DIMS} dimensions...")
    if hasattr(vs, "delete_col"):
        vs.delete_col()
    if hasattr(vs, "create_col"):
        vs.create_col(vector_size=EMBEDDING_MODEL_DIMS, distance="Cosine")
        logger.info("Collection recreated")
    else:
        logger.warning("Vector store doesn't support create_col — may fail if dims mismatch")

    # Re-embed and insert all memories
    failed = 0
    start = time.time()

    for i in range(0, total, args.batch_size):
        batch = memories[i : i + args.batch_size]
        batch_num = i // args.batch_size + 1
        batch_total = (total + args.batch_size - 1) // args.batch_size

        for mem in batch:
            mem_id = mem["id"]
            text = mem["memory"]

            # Reconstruct minimal metadata for the vector store payload
            metadata = {
                "data": text,
                "agent_id": "clara",
            }
            if mem.get("actor_id"):
                metadata["actor_id"] = mem["actor_id"]
            if mem.get("role"):
                metadata["role"] = mem["role"]

            try:
                embedding = PALACE.embedding_model.embed(text, "add")
                vs.insert(
                    vectors=[embedding],
                    payloads=[metadata],
                    ids=[mem_id],
                )
            except Exception as e:
                logger.error(f"Failed to re-embed memory {mem_id}: {e}")
                failed += 1

        elapsed = time.time() - start
        rate = (i + len(batch)) / elapsed if elapsed > 0 else 0
        logger.info(f"Batch {batch_num}/{batch_total} done ({i + len(batch)}/{total}, {rate:.1f} mem/s)")

    elapsed = time.time() - start
    succeeded = total - failed
    logger.info(f"Migration complete: {succeeded}/{total} re-embedded in {elapsed:.1f}s ({failed} failed)")


if __name__ == "__main__":
    main()
