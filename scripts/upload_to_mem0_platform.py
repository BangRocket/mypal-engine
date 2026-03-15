#!/usr/bin/env python3
"""Upload Clara's memories from local Qdrant to mem0 platform.

Reads all memories from the local Qdrant vector store and uploads them
to mem0's managed platform using direct import (infer=False).

Usage:
    # Dry run (count and preview memories)
    python scripts/upload_to_mem0_platform.py --dry-run

    # Upload all memories
    MEM0_PLATFORM_API_KEY=your-key python scripts/upload_to_mem0_platform.py

    # Upload with specific batch size and delay
    python scripts/upload_to_mem0_platform.py --batch-size 50 --delay 0.5

    # Filter by user_id
    python scripts/upload_to_mem0_platform.py --user-id discord-123456
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def get_qdrant_client():
    """Create a read-only Qdrant client from environment config."""
    from qdrant_client import QdrantClient

    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")

    if qdrant_url:
        logger.info(f"Connecting to Qdrant at {qdrant_url}")
        params = {"url": qdrant_url}
        if qdrant_api_key:
            params["api_key"] = qdrant_api_key
        return QdrantClient(**params)

    # Local Qdrant
    from mypalclara.core.memory.config import QDRANT_DATA_DIR

    path = str(QDRANT_DATA_DIR)
    logger.info(f"Opening local Qdrant at {path}")
    return QdrantClient(path=path)


def scroll_all_memories(client, collection_name: str, filters: dict | None = None, batch_size: int = 100):
    """Scroll through all memories in Qdrant, yielding points in batches."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    scroll_filter = None
    if filters:
        conditions = []
        for key, value in filters.items():
            if value is not None:
                conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
        if conditions:
            scroll_filter = Filter(must=conditions)

    offset = None
    total = 0

    while True:
        points, next_offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )

        if not points:
            break

        total += len(points)
        yield points

        if next_offset is None:
            break
        offset = next_offset

    logger.info(f"Scrolled {total} total memories from Qdrant")


def upload_to_mem0(api_key: str, memories: list, dry_run: bool = False, delay: float = 0.1):
    """Upload memories to mem0 platform using direct import.

    Each memory is uploaded as a single user message with infer=False,
    preserving the original user_id and metadata.
    """
    from mem0 import MemoryClient

    if dry_run:
        logger.info(f"[DRY RUN] Would upload {len(memories)} memories")
        for i, mem in enumerate(memories[:5]):
            payload = mem.payload
            logger.info(
                f"  [{i+1}] user_id={payload.get('user_id', 'N/A')} | "
                f"{payload.get('data', '')[:80]}..."
            )
        if len(memories) > 5:
            logger.info(f"  ... and {len(memories) - 5} more")
        return 0, 0

    client = MemoryClient(api_key=api_key)

    uploaded = 0
    failed = 0

    for i, mem in enumerate(memories):
        payload = mem.payload
        memory_text = payload.get("data", "")
        if not memory_text:
            logger.warning(f"  Skipping memory {mem.id} — empty data field")
            failed += 1
            continue

        user_id = payload.get("user_id")
        agent_id = payload.get("agent_id")
        run_id = payload.get("run_id")

        # Build metadata from remaining payload fields
        skip_keys = {"data", "hash", "user_id", "agent_id", "run_id"}
        metadata = {k: v for k, v in payload.items() if k not in skip_keys and v is not None}

        # mem0 direct import: only "user" role messages are stored
        messages = [{"role": "user", "content": memory_text}]

        kwargs = {"user_id": user_id or "default", "infer": False}
        if agent_id:
            kwargs["agent_id"] = agent_id
        if run_id:
            kwargs["run_id"] = run_id
        if metadata:
            kwargs["metadata"] = metadata

        try:
            client.add(messages, **kwargs)
            uploaded += 1
            if (i + 1) % 25 == 0:
                logger.info(f"  Progress: {i+1}/{len(memories)} uploaded")
        except Exception as e:
            logger.error(f"  Failed to upload memory {mem.id}: {e}")
            failed += 1

        if delay > 0:
            time.sleep(delay)

    return uploaded, failed


def main():
    parser = argparse.ArgumentParser(description="Upload Clara's memories to mem0 platform")
    parser.add_argument("--dry-run", action="store_true", help="Preview without uploading")
    parser.add_argument("--batch-size", type=int, default=100, help="Qdrant scroll batch size (default: 100)")
    parser.add_argument("--delay", type=float, default=0.1, help="Delay between uploads in seconds (default: 0.1)")
    parser.add_argument("--user-id", type=str, help="Filter by user_id")
    parser.add_argument("--collection", type=str, default="clara_memories", help="Qdrant collection name")
    parser.add_argument(
        "--api-key",
        type=str,
        default=os.getenv("MEM0_PLATFORM_API_KEY"),
        help="mem0 platform API key (or set MEM0_PLATFORM_API_KEY)",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.api_key:
        logger.error("No API key provided. Set MEM0_PLATFORM_API_KEY or use --api-key")
        sys.exit(1)

    # Connect to Qdrant
    qdrant = get_qdrant_client()

    # Check collection exists
    try:
        info = qdrant.get_collection(args.collection)
        logger.info(f"Collection '{args.collection}': {info.points_count} points")
    except Exception as e:
        logger.error(f"Failed to access collection '{args.collection}': {e}")
        sys.exit(1)

    # Scroll all memories
    filters = {}
    if args.user_id:
        filters["user_id"] = args.user_id
        logger.info(f"Filtering by user_id: {args.user_id}")

    all_memories = []
    for batch in scroll_all_memories(qdrant, args.collection, filters=filters or None, batch_size=args.batch_size):
        all_memories.extend(batch)

    if not all_memories:
        logger.info("No memories found. Nothing to upload.")
        return

    logger.info(f"Found {len(all_memories)} memories to upload")

    # Show user_id distribution
    user_ids = {}
    for mem in all_memories:
        uid = mem.payload.get("user_id", "unknown")
        user_ids[uid] = user_ids.get(uid, 0) + 1
    logger.info("User distribution:")
    for uid, count in sorted(user_ids.items(), key=lambda x: -x[1]):
        logger.info(f"  {uid}: {count} memories")

    # Upload
    uploaded, failed = upload_to_mem0(
        api_key=args.api_key or "",
        memories=all_memories,
        dry_run=args.dry_run,
        delay=args.delay,
    )

    if not args.dry_run:
        logger.info(f"Done. Uploaded: {uploaded}, Failed: {failed}, Total: {len(all_memories)}")


if __name__ == "__main__":
    main()
