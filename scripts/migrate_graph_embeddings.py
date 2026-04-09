#!/usr/bin/env python3
"""Re-embed FalkorDB graph entities after switching embedding model.

Exports all triples and entity metadata, drops the graph, recreates it
with new dimensions, and re-embeds all entities.

Usage:
    # Dry run (count entities and triples)
    python scripts/migrate_graph_embeddings.py --dry-run

    # Full migration
    python scripts/migrate_graph_embeddings.py
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
    parser = argparse.ArgumentParser(description="Re-embed FalkorDB graph entities")
    parser.add_argument("--dry-run", action="store_true", help="Count entities without migrating")
    args = parser.parse_args()

    import os

    if os.getenv("ENABLE_GRAPH_MEMORY", "false").lower() != "true":
        logger.error("Graph memory is disabled (ENABLE_GRAPH_MEMORY != true)")
        sys.exit(1)

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

    if not hasattr(PALACE, "graph") or PALACE.graph is None:
        logger.error("Graph store not initialized")
        sys.exit(1)

    graph_store = PALACE.graph
    graph = graph_store.graph

    # Step 1: Export all triples and entity metadata
    logger.info("Exporting triples from graph...")
    try:
        result = graph.query("""
            MATCH (s:__Entity__)-[r]->(d:__Entity__)
            RETURN s.name AS source, s.user_id AS user_id,
                   s.mentions AS s_mentions, s.created_at AS s_created,
                   type(r) AS relationship,
                   r.mentions AS r_mentions, r.created_at AS r_created,
                   d.name AS destination, d.user_id AS d_user_id,
                   d.mentions AS d_mentions, d.created_at AS d_created
        """)
        triples = []
        for row in result.result_set:
            triples.append({
                "source": row[0],
                "user_id": row[1],
                "s_mentions": row[2],
                "s_created": row[3],
                "relationship": row[4],
                "r_mentions": row[5],
                "r_created": row[6],
                "destination": row[7],
                "d_user_id": row[8],
                "d_mentions": row[9],
                "d_created": row[10],
            })
    except Exception as e:
        logger.error(f"Failed to export triples: {e}")
        sys.exit(1)

    logger.info(f"Found {len(triples)} triples")

    if args.dry_run:
        logger.info("Dry run — no changes made")
        if triples:
            t = triples[0]
            logger.info(f"Sample: ({t['source']}) -[{t['relationship']}]-> ({t['destination']})")
        return

    if not triples:
        logger.info("No triples to migrate — dropping and recreating graph")
        graph.delete()
        graph_store._create_indexes()
        logger.info("Done")
        return

    # Step 2: Drop the graph
    logger.info("Dropping graph...")
    graph.delete()

    # Step 3: Recreate indexes with new dimensions
    logger.info(f"Recreating indexes with {EMBEDDING_MODEL_DIMS} dimensions...")
    graph_store._create_indexes()

    # Step 4: Re-embed entities and reinsert triples
    embedder = PALACE.embedding_model
    failed = 0
    start = time.time()

    for i, triple in enumerate(triples):
        source = triple["source"]
        destination = triple["destination"]
        relationship = triple["relationship"]
        user_id = triple["user_id"] or triple["d_user_id"]

        if not source or not destination or not relationship or not user_id:
            logger.warning(f"Skipping incomplete triple: {triple}")
            failed += 1
            continue

        try:
            source_embedding = embedder.embed(source, "add")
            dest_embedding = embedder.embed(destination, "add")

            cypher = f"""
            MERGE (s:__Entity__ {{name: $source_name, user_id: $user_id}})
            ON CREATE SET s.created_at = $s_created, s.mentions = $s_mentions
            SET s.embedding = vecf32($source_embedding)
            WITH s
            MERGE (d:__Entity__ {{name: $dest_name, user_id: $user_id}})
            ON CREATE SET d.created_at = $d_created, d.mentions = $d_mentions
            SET d.embedding = vecf32($dest_embedding)
            WITH s, d
            MERGE (s)-[r:{relationship}]->(d)
            ON CREATE SET r.created_at = $r_created, r.mentions = $r_mentions
            RETURN s.name AS source, type(r) AS relationship, d.name AS target
            """

            graph.query(
                cypher,
                params={
                    "source_name": source,
                    "dest_name": destination,
                    "user_id": user_id,
                    "source_embedding": source_embedding,
                    "dest_embedding": dest_embedding,
                    "s_created": triple.get("s_created"),
                    "s_mentions": triple.get("s_mentions", 1),
                    "d_created": triple.get("d_created"),
                    "d_mentions": triple.get("d_mentions", 1),
                    "r_created": triple.get("r_created"),
                    "r_mentions": triple.get("r_mentions", 1),
                },
            )
        except Exception as e:
            logger.error(f"Failed to reinsert triple ({source})-[{relationship}]->({destination}): {e}")
            failed += 1

        if (i + 1) % 25 == 0 or i == len(triples) - 1:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            logger.info(f"Progress: {i + 1}/{len(triples)} ({rate:.1f} triples/s)")

    elapsed = time.time() - start
    succeeded = len(triples) - failed
    logger.info(f"Migration complete: {succeeded}/{len(triples)} triples in {elapsed:.1f}s ({failed} failed)")


if __name__ == "__main__":
    main()
