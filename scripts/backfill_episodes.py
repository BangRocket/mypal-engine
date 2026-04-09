#!/usr/bin/env python3
"""Backfill episodes from existing conversation history.

Reads past conversation threads from the database and runs episode
extraction on each, populating the episode store with historical data.

Usage:
    # Dry run — show threads that would be processed
    python scripts/backfill_episodes.py --dry-run

    # Process all threads
    python scripts/backfill_episodes.py

    # Process only threads for a specific user
    python scripts/backfill_episodes.py --user discord-271274659385835521

    # Limit to recent threads
    python scripts/backfill_episodes.py --days 30

    # Control batch size
    python scripts/backfill_episodes.py --batch 10
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def fetch_threads(user_id: str | None = None, days: int | None = None) -> list[dict]:
    """Fetch conversation threads from the database.

    Returns list of dicts with id, user_id, context_id, message_count, last_activity_at.
    """
    from sqlalchemy import func, text

    from mypalclara.db import SessionLocal
    from mypalclara.db.models import Message, Session

    db = SessionLocal()
    try:
        query = (
            db.query(
                Session.id,
                Session.user_id,
                Session.context_id,
                Session.last_activity_at,
                func.count(Message.id).label("message_count"),
            )
            .join(Message, Message.session_id == Session.id)
            .group_by(Session.id)
            .having(func.count(Message.id) >= 4)  # Need at least 4 messages
            .order_by(Session.last_activity_at.desc())
        )

        if user_id:
            query = query.filter(Session.user_id == user_id)

        if days:
            cutoff = datetime.now(UTC) - timedelta(days=days)
            query = query.filter(Session.last_activity_at >= cutoff)

        results = query.all()
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "context_id": r.context_id,
                "message_count": r.message_count,
                "last_activity_at": r.last_activity_at,
            }
            for r in results
        ]
    finally:
        db.close()


def fetch_thread_messages(thread_id: str) -> list[dict]:
    """Fetch messages for a specific thread."""
    from mypalclara.db import SessionLocal
    from mypalclara.db.models import Message

    db = SessionLocal()
    try:
        messages = (
            db.query(Message)
            .filter(Message.session_id == thread_id)
            .order_by(Message.created_at.asc())
            .limit(50)
            .all()
        )
        return [
            {
                "role": msg.role,
                "content": msg.content,
                "name": msg.user_id if msg.role == "user" else "Clara",
                "timestamp": msg.created_at.isoformat() if msg.created_at else "",
            }
            for msg in messages
        ]
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill episodes from conversation history")
    parser.add_argument("--dry-run", action="store_true", help="Show threads without processing")
    parser.add_argument("--user", type=str, help="Only process threads for this user ID")
    parser.add_argument("--days", type=int, help="Only process threads from the last N days")
    parser.add_argument("--batch", type=int, default=25, help="Threads per batch (default: 25)")
    args = parser.parse_args()

    from mypalclara.core.memory.config import ROOK

    if ROOK is None:
        logger.error("Rook not initialized — check your configuration")
        sys.exit(1)

    # Initialize memory manager for reflection
    from mypalclara.core import make_llm
    from mypalclara.core.memory_manager import MemoryManager

    llm = make_llm()
    mm = MemoryManager.initialize(llm_callable=llm)

    if not mm.episode_store:
        logger.error("Episode store not available — check Qdrant connection")
        sys.exit(1)

    # Fetch threads
    logger.info("Fetching conversation threads...")
    threads = fetch_threads(user_id=args.user, days=args.days)
    logger.info(f"Found {len(threads)} threads with 4+ messages")

    if args.dry_run:
        for t in threads[:20]:
            logger.info(
                f"  Thread {t['id'][:8]}... | user={t['user_id']} | "
                f"msgs={t['message_count']} | updated={t['last_activity_at']}"
            )
        if len(threads) > 20:
            logger.info(f"  ... and {len(threads) - 20} more")
        logger.info("Dry run — no changes made")
        return

    if not threads:
        logger.info("No threads to process")
        return

    total_episodes = 0
    total_errors = 0
    start = time.time()

    for i, thread in enumerate(threads):
        thread_id = thread["id"]
        user_id = thread["user_id"]

        logger.info(
            f"[{i + 1}/{len(threads)}] Processing thread {thread_id[:8]}... "
            f"(user={user_id}, msgs={thread['message_count']})"
        )

        try:
            messages = fetch_thread_messages(thread_id)
            if len(messages) < 4:
                continue

            result = mm.reflect_on_session(
                messages, user_id, session_id=thread.get("context_id")
            )

            if result:
                ep_count = len(result.get("episodes", []))
                total_episodes += ep_count
                logger.info(f"  → {ep_count} episodes extracted")
            else:
                logger.info("  → No episodes (reflection returned empty)")

        except Exception as e:
            logger.error(f"  → Failed: {e}")
            total_errors += 1

        # Progress logging
        if (i + 1) % args.batch == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            logger.info(f"Progress: {i + 1}/{len(threads)} threads ({rate:.1f}/s)")

    elapsed = time.time() - start
    logger.info(
        f"Backfill complete: {total_episodes} episodes from {len(threads)} threads "
        f"in {elapsed:.1f}s ({total_errors} errors)"
    )


if __name__ == "__main__":
    main()
