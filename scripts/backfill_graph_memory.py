#!/usr/bin/env python3
"""
Backfill graph memory from existing Discord chat history.

This script processes existing conversations in the database through mem0.add()
to extract entity relationships for the graph memory.

Usage:
    poetry run python scripts/backfill_graph_memory.py              # Dry run
    poetry run python scripts/backfill_graph_memory.py --apply      # Actually process
    poetry run python scripts/backfill_graph_memory.py --user <id>  # Specific user only
    poetry run python scripts/backfill_graph_memory.py --limit 100  # Limit sessions
    poetry run python scripts/backfill_graph_memory.py --resume     # Resume from progress file
    poetry run python scripts/backfill_graph_memory.py --parallel 4 # Process 4 sessions concurrently
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

# Suppress noisy loggers from dependencies BEFORE importing them
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("clara_core.memory").setLevel(logging.WARNING)

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from db import SessionLocal
from db.models import Message, Session

# Configure logging - only show our messages
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",  # Clean format without timestamps for readability
)
logger = logging.getLogger(__name__)

# Progress file for resuming
PROGRESS_FILE = Path(__file__).parent.parent / ".graph_backfill_progress.json"

# Agent ID to use for graph memory (should match your bot)
AGENT_ID = os.getenv("BOT_NAME", "clara").lower()


def format_date(dt: datetime | None) -> str:
    """Format datetime for display."""
    if dt is None:
        return "unknown"
    return dt.strftime("%Y-%m-%d %H:%M")


def format_duration(start: datetime | None, end: datetime | None) -> str:
    """Format duration between two datetimes."""
    if not start or not end:
        return ""
    delta = end - start
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"


def load_progress() -> dict:
    """Load progress from file."""
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_progress(progress: dict) -> None:
    """Save progress to file."""
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2))


def get_sessions_to_process(
    db: OrmSession,
    user_id: str | None = None,
    limit: int | None = None,
    processed_ids: set[str] | None = None,
) -> list[Session]:
    """Get sessions to process, optionally filtered by user."""
    query = db.query(Session).order_by(Session.started_at.asc())

    if user_id:
        query = query.filter(Session.user_id == user_id)

    if processed_ids:
        query = query.filter(~Session.id.in_(processed_ids))

    if limit:
        query = query.limit(limit)

    return query.all()


def get_date_range(sessions: list[Session]) -> tuple[datetime | None, datetime | None]:
    """Get the date range of sessions."""
    if not sessions:
        return None, None

    starts = [s.started_at for s in sessions if s.started_at]
    ends = [s.last_activity_at for s in sessions if s.last_activity_at]

    return (min(starts) if starts else None, max(ends) if ends else None)


def get_session_messages(db: OrmSession, session_id: str) -> list[Message]:
    """Get all messages for a session ordered by time."""
    return db.query(Message).filter(Message.session_id == session_id).order_by(Message.created_at.asc()).all()


def chunk_messages(messages: list[Message], chunk_size: int = 4) -> list[list[dict]]:
    """
    Chunk messages into conversation slices for processing.

    Each chunk contains up to chunk_size messages, preserving conversation flow.
    Overlaps by 2 messages to maintain context.
    """
    if not messages:
        return []

    chunks = []
    msg_dicts = [{"role": m.role, "content": m.content} for m in messages]

    for i in range(0, len(msg_dicts), chunk_size - 2):
        chunk = msg_dicts[i : i + chunk_size]
        if len(chunk) >= 2:  # Need at least a user-assistant pair
            chunks.append(chunk)

    return chunks


async def process_chunk_async(
    chunk: list[dict],
    chunk_idx: int,
    user_id: str,
    project_id: str,
    mem0,
    semaphore: asyncio.Semaphore,
) -> dict:
    """Process a single chunk asynchronously."""
    async with semaphore:
        try:
            # Use async add if available, otherwise run sync in thread
            if hasattr(mem0, "aadd"):
                result = await mem0.aadd(
                    chunk,
                    user_id=user_id,
                    agent_id=AGENT_ID,
                    metadata={"project_id": project_id, "backfill": True},
                )
            else:
                result = await asyncio.to_thread(
                    mem0.add,
                    chunk,
                    user_id=user_id,
                    agent_id=AGENT_ID,
                    metadata={"project_id": project_id, "backfill": True},
                )

            memories_added = 0
            graph_entities_added = 0

            if isinstance(result, dict):
                memories_added = len(result.get("results", []))
                relations = result.get("relations", {})
                if isinstance(relations, dict):
                    graph_entities_added = len(relations.get("added_entities", []))

            return {
                "success": True,
                "memories_added": memories_added,
                "graph_entities_added": graph_entities_added,
            }

        except Exception as e:
            return {
                "success": False,
                "error": f"Chunk {chunk_idx}: {str(e)}",
            }


async def process_session_async(
    session: Session,
    messages: list[Message],
    mem0,
    chunk_semaphore: asyncio.Semaphore,
    dry_run: bool = True,
    verbose: bool = False,
) -> dict:
    """
    Process a session's messages to extract graph relations (async version).

    Returns stats about what was processed.
    """
    user_id = session.user_id
    project_id = session.project_id

    chunks = chunk_messages(messages)
    stats = {
        "session_id": session.id,
        "user_id": user_id,
        "message_count": len(messages),
        "chunk_count": len(chunks),
        "graph_entities_added": 0,
        "memories_added": 0,
        "errors": [],
    }

    if dry_run:
        return stats

    # Process all chunks concurrently with semaphore limiting
    tasks = [
        process_chunk_async(chunk, i, user_id, project_id, mem0, chunk_semaphore) for i, chunk in enumerate(chunks)
    ]
    results = await asyncio.gather(*tasks)

    for result in results:
        if result["success"]:
            stats["memories_added"] += result["memories_added"]
            stats["graph_entities_added"] += result["graph_entities_added"]
        else:
            stats["errors"].append(result["error"])
            if verbose:
                logger.warning(f"    ⚠ {result['error']}")

    return stats


def process_session(
    session: Session,
    messages: list[Message],
    mem0,
    dry_run: bool = True,
    verbose: bool = False,
) -> dict:
    """
    Process a session's messages to extract graph relations (sync version).

    Returns stats about what was processed.
    """
    user_id = session.user_id
    project_id = session.project_id

    chunks = chunk_messages(messages)
    stats = {
        "session_id": session.id,
        "user_id": user_id,
        "message_count": len(messages),
        "chunk_count": len(chunks),
        "graph_entities_added": 0,
        "memories_added": 0,
        "errors": [],
    }

    if dry_run:
        return stats

    for i, chunk in enumerate(chunks):
        try:
            result = mem0.add(
                chunk,
                user_id=user_id,
                agent_id=AGENT_ID,
                metadata={"project_id": project_id, "backfill": True},
            )

            if isinstance(result, dict):
                # Count memories added
                stats["memories_added"] += len(result.get("results", []))

                # Count graph entities
                relations = result.get("relations", {})
                if isinstance(relations, dict):
                    added = relations.get("added_entities", [])
                    stats["graph_entities_added"] += len(added)

        except Exception as e:
            error_msg = f"Chunk {i}: {str(e)}"
            stats["errors"].append(error_msg)
            if verbose:
                logger.warning(f"    ⚠ Error in chunk {i}: {e}")

    return stats


async def run_parallel(
    sessions: list[Session],
    db: OrmSession,
    mem0,
    dry_run: bool,
    verbose: bool,
    parallel: int,
    processed_ids: set[str],
    progress: dict,
) -> dict:
    """Run backfill with parallel session processing."""
    total_sessions = len(sessions)
    total_stats = {
        "sessions_processed": 0,
        "messages_processed": 0,
        "chunks_processed": 0,
        "graph_entities_added": 0,
        "memories_added": 0,
        "errors": 0,
    }

    # Semaphore to limit concurrent LLM calls (chunks across all sessions)
    # Use parallel * 2 to allow some overlap while limiting API pressure
    chunk_semaphore = asyncio.Semaphore(parallel * 2)

    # Session semaphore to limit concurrent sessions
    session_semaphore = asyncio.Semaphore(parallel)

    current_date = None
    start_time = datetime.now()

    async def process_one_session(idx: int, session: Session) -> tuple[int, dict]:
        """Process a single session with semaphore."""
        async with session_semaphore:
            messages = get_session_messages(db, session.id)
            if not messages:
                return idx, None

            stats = await process_session_async(
                session, messages, mem0, chunk_semaphore, dry_run=dry_run, verbose=verbose
            )
            return idx, stats

    # Process sessions in batches for better progress reporting
    batch_size = parallel * 2
    for batch_start in range(0, total_sessions, batch_size):
        batch_end = min(batch_start + batch_size, total_sessions)
        batch_sessions = sessions[batch_start:batch_end]

        # Create tasks for this batch
        tasks = [process_one_session(batch_start + i, session) for i, session in enumerate(batch_sessions)]

        # Wait for batch to complete
        results = await asyncio.gather(*tasks)

        # Process results and update progress
        for idx, stats in results:
            if stats is None:
                continue

            session = sessions[idx]
            pct = ((idx + 1) / total_sessions) * 100

            # Show date header when date changes
            session_date = session.started_at.date() if session.started_at else None
            if session_date and session_date != current_date:
                current_date = session_date
                logger.info("")
                logger.info(f"─── {current_date.strftime('%A, %B %d, %Y')} ───")

            # Update totals
            total_stats["sessions_processed"] += 1
            total_stats["messages_processed"] += stats["message_count"]
            total_stats["chunks_processed"] += stats["chunk_count"]
            total_stats["graph_entities_added"] += stats["graph_entities_added"]
            total_stats["memories_added"] += stats["memories_added"]
            total_stats["errors"] += len(stats["errors"])

            # Status line
            session_time = format_date(session.started_at) if session.started_at else "unknown"
            user_display = session.user_id
            if len(user_display) > 25:
                user_display = user_display[:22] + "..."

            if dry_run:
                logger.info(
                    f"  [{idx + 1:3}/{total_sessions}] {pct:5.1f}% │ "
                    f"{session_time[11:16]} │ "
                    f"{stats['message_count']:3} msgs, {stats['chunk_count']:2} chunks │ "
                    f"{user_display}"
                )
            else:
                graph_icon = "🔗" if stats["graph_entities_added"] > 0 else "  "
                mem_icon = "💾" if stats["memories_added"] > 0 else "  "
                err_icon = "⚠" if stats["errors"] else " "

                logger.info(
                    f"  [{idx + 1:3}/{total_sessions}] {pct:5.1f}% │ "
                    f"{session_time[11:16]} │ "
                    f"{graph_icon} +{stats['graph_entities_added']:2} "
                    f"{mem_icon} +{stats['memories_added']:2} "
                    f"{err_icon}│ {user_display}"
                )

            # Save progress after each session (if not dry run)
            if not dry_run:
                processed_ids.add(session.id)
                progress["processed_session_ids"] = list(processed_ids)
                progress["last_updated"] = datetime.now().isoformat()
                progress["stats"] = total_stats
                save_progress(progress)

    return total_stats


def main():
    parser = argparse.ArgumentParser(description="Backfill graph memory from existing chat history")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually process (default is dry run)",
    )
    parser.add_argument(
        "--user",
        type=str,
        help="Only process sessions for this user ID",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of sessions to process",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last progress",
    )
    parser.add_argument(
        "--clear-progress",
        action="store_true",
        help="Clear progress file and start fresh",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed progress for each chunk",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=1,
        help="Number of sessions to process in parallel (default: 1)",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    logger.info("=" * 60)
    logger.info("Graph Memory Backfill")
    logger.info("=" * 60)

    # Check if graph memory is enabled
    logger.info("Initializing mem0...")
    from clara_core.memory import ENABLE_GRAPH_MEMORY, ROOK

    if not ENABLE_GRAPH_MEMORY:
        logger.error("✗ Graph memory is not enabled. Set ENABLE_GRAPH_MEMORY=true")
        sys.exit(1)

    if ROOK is None:
        logger.error("✗ mem0 failed to initialize. Check your configuration.")
        sys.exit(1)

    logger.info("✓ mem0 initialized with graph memory enabled")

    if args.parallel > 1:
        logger.info(f"✓ Parallel mode: {args.parallel} concurrent sessions")

    # Handle progress
    progress = {}
    processed_ids = set()

    if args.clear_progress and PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
        logger.info("✓ Cleared progress file")

    if args.resume:
        progress = load_progress()
        processed_ids = set(progress.get("processed_session_ids", []))
        if processed_ids:
            logger.info(f"✓ Resuming: {len(processed_ids)} sessions already processed")

    # Get sessions to process
    logger.info("Querying database for sessions...")
    db = SessionLocal()
    try:
        sessions = get_sessions_to_process(
            db,
            user_id=args.user,
            limit=args.limit,
            processed_ids=processed_ids if args.resume else None,
        )

        total_sessions = len(sessions)

        if total_sessions == 0:
            logger.info("No sessions to process.")
            return

        # Get date range for context
        date_start, date_end = get_date_range(sessions)

        logger.info("")
        logger.info(f"Sessions to process: {total_sessions}")
        if date_start and date_end:
            logger.info(f"Date range: {format_date(date_start)} → {format_date(date_end)}")
            logger.info(f"Time span: {format_duration(date_start, date_end)}")

        if args.user:
            logger.info(f"Filtered to user: {args.user}")

        logger.info("")

        if dry_run:
            logger.info("┌" + "─" * 58 + "┐")
            logger.info("│" + " DRY RUN - No changes will be made".center(58) + "│")
            logger.info("│" + " Run with --apply to actually process".center(58) + "│")
            logger.info("└" + "─" * 58 + "┘")
            logger.info("")

        start_time = datetime.now()

        # Use async processing if parallel > 1
        if args.parallel > 1:
            total_stats = asyncio.run(
                run_parallel(sessions, db, ROOK, dry_run, args.verbose, args.parallel, processed_ids, progress)
            )
        else:
            # Original sequential processing
            total_stats = {
                "sessions_processed": 0,
                "messages_processed": 0,
                "chunks_processed": 0,
                "graph_entities_added": 0,
                "memories_added": 0,
                "errors": 0,
            }

            current_date = None

            for i, session in enumerate(sessions):
                messages = get_session_messages(db, session.id)

                if not messages:
                    continue

                # Show date header when date changes
                session_date = session.started_at.date() if session.started_at else None
                if session_date and session_date != current_date:
                    current_date = session_date
                    logger.info("")
                    logger.info(f"─── {current_date.strftime('%A, %B %d, %Y')} ───")

                pct = ((i + 1) / total_sessions) * 100
                session_time = format_date(session.started_at) if session.started_at else "unknown"

                user_display = session.user_id
                if len(user_display) > 25:
                    user_display = user_display[:22] + "..."

                stats = process_session(session, messages, ROOK, dry_run=dry_run, verbose=args.verbose)

                # Update totals
                total_stats["sessions_processed"] += 1
                total_stats["messages_processed"] += stats["message_count"]
                total_stats["chunks_processed"] += stats["chunk_count"]
                total_stats["graph_entities_added"] += stats["graph_entities_added"]
                total_stats["memories_added"] += stats["memories_added"]
                total_stats["errors"] += len(stats["errors"])

                # Status line
                if dry_run:
                    logger.info(
                        f"  [{i + 1:3}/{total_sessions}] {pct:5.1f}% │ "
                        f"{session_time[11:16]} │ "
                        f"{stats['message_count']:3} msgs, {stats['chunk_count']:2} chunks │ "
                        f"{user_display}"
                    )
                else:
                    graph_icon = "🔗" if stats["graph_entities_added"] > 0 else "  "
                    mem_icon = "💾" if stats["memories_added"] > 0 else "  "
                    err_icon = "⚠" if stats["errors"] else " "

                    logger.info(
                        f"  [{i + 1:3}/{total_sessions}] {pct:5.1f}% │ "
                        f"{session_time[11:16]} │ "
                        f"{graph_icon} +{stats['graph_entities_added']:2} "
                        f"{mem_icon} +{stats['memories_added']:2} "
                        f"{err_icon}│ {user_display}"
                    )

                # Save progress after each session (if not dry run)
                if not dry_run:
                    processed_ids.add(session.id)
                    progress["processed_session_ids"] = list(processed_ids)
                    progress["last_updated"] = datetime.now().isoformat()
                    progress["stats"] = total_stats
                    save_progress(progress)

        # Calculate elapsed time
        elapsed_str = format_duration(start_time, datetime.now())

        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("SUMMARY")
        logger.info("=" * 60)
        logger.info(f"  Sessions processed:    {total_stats['sessions_processed']:,}")
        logger.info(f"  Messages processed:    {total_stats['messages_processed']:,}")
        logger.info(f"  Chunks processed:      {total_stats['chunks_processed']:,}")
        logger.info(f"  Graph entities added:  {total_stats['graph_entities_added']:,}")
        logger.info(f"  Memories added:        {total_stats['memories_added']:,}")
        logger.info(f"  Errors:                {total_stats['errors']:,}")
        logger.info(f"  Time elapsed:          {elapsed_str}")
        if args.parallel > 1:
            logger.info(f"  Parallelism:           {args.parallel}x")

        if dry_run:
            logger.info("")
            logger.info("This was a DRY RUN. Run with --apply to actually process.")
        else:
            logger.info("")
            logger.info(f"✓ Progress saved to {PROGRESS_FILE.name}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
