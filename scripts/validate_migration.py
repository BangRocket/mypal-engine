#!/usr/bin/env python3
"""Validate migration by comparing search results between pgvector and Qdrant.

Performs semantic search comparisons to ensure Qdrant produces
equivalent results to pgvector before cutover.

Usage:
    # Basic validation with sample queries
    python scripts/validate_migration.py

    # Extended validation with more samples
    python scripts/validate_migration.py --sample-size 1000

    # Compare specific queries
    python scripts/validate_migration.py --queries "hello world" "how are you"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
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
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
COLLECTION_NAME = os.getenv("MEM0_COLLECTION_NAME", "clara_memories")
EMBEDDING_MODEL = "text-embedding-3-small"


@dataclass
class SearchResult:
    """Search result from either store."""

    id: str
    score: float
    memory: str


@dataclass
class ComparisonResult:
    """Result of comparing search results."""

    query: str
    pgvector_count: int
    qdrant_count: int
    overlap_count: int
    overlap_rate: float
    top_k_match: bool
    top_k: int


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


def get_embedding(text: str) -> list[float]:
    """Get embedding from OpenAI."""
    if not OPENAI_API_KEY:
        logger.error("OPENAI_API_KEY not set")
        sys.exit(1)

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.embeddings.create(
        input=[text],
        model=EMBEDDING_MODEL,
    )
    return response.data[0].embedding


def search_pgvector(
    engine,
    query_embedding: list[float],
    limit: int = 10,
    user_id: str | None = None,
) -> list[SearchResult]:
    """Search pgvector for similar vectors."""
    # Build filter clause
    filter_clause = ""
    params = {"embedding": str(query_embedding), "limit": limit}

    if user_id:
        filter_clause = "AND payload->>'user_id' = :user_id"
        params["user_id"] = user_id

    query = text(f"""
        SELECT id, payload, embedding <=> :embedding::vector as distance
        FROM {COLLECTION_NAME}
        WHERE true {filter_clause}
        ORDER BY distance
        LIMIT :limit
    """)

    with engine.connect() as conn:
        result = conn.execute(query, params)
        results = []
        for row in result:
            payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload or "{}")
            results.append(
                SearchResult(
                    id=str(row.id),
                    score=1 - row.distance,  # Convert distance to similarity
                    memory=payload.get("memory", ""),
                )
            )
        return results


def search_qdrant(
    client: QdrantClient,
    query_embedding: list[float],
    limit: int = 10,
    user_id: str | None = None,
) -> list[SearchResult]:
    """Search Qdrant for similar vectors."""
    query_filter = None
    if user_id:
        query_filter = Filter(must=[FieldCondition(key="user_id", match=MatchValue(value=user_id))])

    hits = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_embedding,
        query_filter=query_filter,
        limit=limit,
    )

    results = []
    for hit in hits.points:
        payload = hit.payload or {}
        results.append(
            SearchResult(
                id=str(hit.id),
                score=hit.score,
                memory=payload.get("memory", ""),
            )
        )
    return results


def compare_results(
    pg_results: list[SearchResult],
    qdrant_results: list[SearchResult],
    query: str,
    top_k: int = 5,
) -> ComparisonResult:
    """Compare search results from both stores."""
    pg_ids = {r.id for r in pg_results}
    qdrant_ids = {r.id for r in qdrant_results}
    overlap = pg_ids & qdrant_ids

    # Check top-k match
    pg_top_k = {r.id for r in pg_results[:top_k]}
    qdrant_top_k = {r.id for r in qdrant_results[:top_k]}
    top_k_overlap = len(pg_top_k & qdrant_top_k)
    top_k_match = top_k_overlap >= top_k * 0.6  # 60% overlap threshold

    return ComparisonResult(
        query=query[:50] + "..." if len(query) > 50 else query,
        pgvector_count=len(pg_results),
        qdrant_count=len(qdrant_results),
        overlap_count=len(overlap),
        overlap_rate=len(overlap) / max(len(pg_ids), len(qdrant_ids)) if pg_ids or qdrant_ids else 1.0,
        top_k_match=top_k_match,
        top_k=top_k,
    )


def get_sample_queries(engine, sample_size: int) -> list[str]:
    """Get sample queries from existing memories."""
    query = text(f"""
        SELECT DISTINCT payload->>'memory' as memory
        FROM {COLLECTION_NAME}
        WHERE payload->>'memory' IS NOT NULL
        ORDER BY RANDOM()
        LIMIT :limit
    """)

    with engine.connect() as conn:
        result = conn.execute(query, {"limit": sample_size})
        return [row.memory for row in result if row.memory]


def main():
    parser = argparse.ArgumentParser(description="Validate pgvector to Qdrant migration")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=100,
        help="Number of sample queries to test (default: 100)",
    )
    parser.add_argument(
        "--queries",
        nargs="+",
        help="Specific queries to test (overrides sample-size)",
    )
    parser.add_argument(
        "--user-id",
        help="Filter by user_id",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of results to fetch per query (default: 10)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Top-k results to compare for match (default: 5)",
    )
    args = parser.parse_args()

    # Get connections
    logger.info("Connecting to databases...")
    engine = get_pgvector_connection()
    qdrant_client = get_qdrant_client()

    # Get queries
    if args.queries:
        queries = args.queries
    else:
        logger.info(f"Fetching {args.sample_size} sample queries from existing memories...")
        queries = get_sample_queries(engine, args.sample_size)
        if not queries:
            logger.error("No sample queries found. Is the database empty?")
            sys.exit(1)
        # Add some generic queries
        queries.extend(
            [
                "hello",
                "how are you",
                "what do you remember",
                "tell me about",
            ]
        )
        random.shuffle(queries)

    logger.info(f"Testing {len(queries)} queries...")

    # Run comparisons
    results: list[ComparisonResult] = []
    errors = 0

    for i, query in enumerate(queries):
        try:
            # Get embedding
            embedding = get_embedding(query[:500])  # Truncate long queries

            # Search both stores
            pg_results = search_pgvector(engine, embedding, args.limit, args.user_id)
            qdrant_results = search_qdrant(qdrant_client, embedding, args.limit, args.user_id)

            # Compare
            comparison = compare_results(pg_results, qdrant_results, query, args.top_k)
            results.append(comparison)

            # Progress
            if (i + 1) % 10 == 0:
                logger.info(f"Progress: {i + 1}/{len(queries)}")

        except Exception as e:
            logger.error(f"Error processing query '{query[:30]}...': {e}")
            errors += 1

    # Summarize results
    print(f"\n{'='*60}")
    print("VALIDATION RESULTS")
    print(f"{'='*60}")
    print(f"Total queries: {len(queries)}")
    print(f"Successful: {len(results)}")
    print(f"Errors: {errors}")

    if results:
        avg_overlap = sum(r.overlap_rate for r in results) / len(results)
        top_k_matches = sum(1 for r in results if r.top_k_match)

        print("\nOverlap Statistics:")
        print(f"  Average overlap rate: {avg_overlap*100:.1f}%")
        print(f"  Top-{args.top_k} match rate: {top_k_matches}/{len(results)} ({top_k_matches/len(results)*100:.1f}%)")

        # Find worst queries
        worst = sorted(results, key=lambda r: r.overlap_rate)[:5]
        if worst and worst[0].overlap_rate < 0.8:
            print("\nWorst performing queries:")
            for r in worst:
                print(f"  - '{r.query}' (overlap: {r.overlap_rate*100:.1f}%)")

        # Verdict
        print(f"\n{'='*60}")
        if avg_overlap >= 0.8 and top_k_matches / len(results) >= 0.6:
            print("VERDICT: PASS - Migration is ready for cutover")
            print(f"{'='*60}")
        else:
            print("VERDICT: FAIL - Review mismatches before cutover")
            print("Consider:")
            print("  - Verifying all records were migrated")
            print("  - Checking for index configuration differences")
            print("  - Running with --sample-size 1000 for more data")
            print(f"{'='*60}")
            sys.exit(1)


if __name__ == "__main__":
    main()
