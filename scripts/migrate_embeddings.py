#!/usr/bin/env python3
"""Re-embed all memories after switching embedding model.

Recreates the Qdrant collection with new dimensions and re-embeds all
existing memories using the currently configured embedding provider.

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


def main():
    parser = argparse.ArgumentParser(description="Re-embed memories after embedding model change")
    parser.add_argument("--dry-run", action="store_true", help="Count memories and show config without migrating")
    parser.add_argument("--batch-size", type=int, default=25, help="Memories per batch (default: 25)")
    args = parser.parse_args()

    from mypalclara.core.memory.config import (
        EMBEDDING_MODEL_DIMS,
        EMBEDDING_PROVIDER,
        ROOK,
        config,
    )

    embedder_config = config.get("embedder", {})
    model = embedder_config.get("config", {}).get("model", "unknown")

    logger.info(f"Embedding provider: {EMBEDDING_PROVIDER}")
    logger.info(f"Embedding model: {model}")
    logger.info(f"Embedding dimensions: {EMBEDDING_MODEL_DIMS}")

    if ROOK is None:
        logger.error("Rook not initialized — check your configuration")
        sys.exit(1)

    # Get all memories
    logger.info("Fetching all memories...")
    all_memories = ROOK.get_all()
    total = len(all_memories.get("results", []))
    logger.info(f"Found {total} memories")

    if args.dry_run:
        logger.info("Dry run — no changes made")
        return

    if total == 0:
        logger.info("No memories to migrate")
        return

    # Recreate the vector store collection with new dimensions
    logger.info(f"Recreating vector store with {EMBEDDING_MODEL_DIMS} dimensions...")
    try:
        vs = ROOK.vector_store
        if hasattr(vs, "delete_col"):
            vs.delete_col()
        if hasattr(vs, "create_col"):
            vs.create_col(
                vector_size=EMBEDDING_MODEL_DIMS,
                distance="cosine",
            )
            logger.info("Vector store collection recreated")
        else:
            logger.warning("Vector store doesn't support create_col — may need manual setup")
    except Exception as e:
        logger.error(f"Failed to recreate collection: {e}")
        sys.exit(1)

    # Re-embed and insert all memories
    memories = all_memories["results"]
    failed = 0
    start = time.time()

    for i in range(0, total, args.batch_size):
        batch = memories[i : i + args.batch_size]
        batch_num = i // args.batch_size + 1
        batch_total = (total + args.batch_size - 1) // args.batch_size

        for mem in batch:
            mem_id = mem.get("id", "unknown")
            text = mem.get("memory", "")
            metadata = mem.get("metadata", {})

            if not text:
                logger.warning(f"Skipping memory {mem_id} — empty text")
                failed += 1
                continue

            try:
                embedding = ROOK.embedding_model.embed(text, "add")
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
