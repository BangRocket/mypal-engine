#!/usr/bin/env python3
"""Backfill Verbatim Chat History from Discord chat exports.

Reads cleaned Discord chat exports (with decision files) and inserts
kept messages into the messages table in chronological order. Skips
messages that already exist (by timestamp + user dedup).

This does NOT run reflection/episodes — just populates the raw history.

Usage:
    # Dry run
    python scripts/backfill_vch.py --dry-run

    # Import all chats
    python scripts/backfill_vch.py

    # Specific file
    python scripts/backfill_vch.py --file "chats/Direct Messages - MyPalClara [...].json"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CLARA_BOTS = {"MyPalClara", "MyPalClarissa"}

USER_REGISTRY = {
    "stairmaster401": ("discord-271274659385835521", "Josh"),
    "cadacious": ("discord-275457539863347201", "cadacious"),
    "MyPalClara": ("clara", "Clara"),
    "MyPalClarissa": ("clara", "Clarissa"),
}


def load_chat_with_decisions(chat_path: Path) -> tuple[dict, list[dict]]:
    """Load chat and filter by decisions. Returns (metadata, kept_messages)."""
    decisions_path = chat_path.with_name(chat_path.stem + "_decisions.json")

    with open(chat_path) as f:
        chat = json.load(f)

    decisions = {}
    if decisions_path.exists():
        with open(decisions_path) as f:
            decisions = json.load(f)

    messages = chat.get("messages", [])
    kept = []

    for m in messages:
        mid = m.get("id", "")
        status = decisions.get(mid, {}).get("status", "keep")
        if status == "remove":
            continue

        content = m.get("content", "").strip()
        if not content:
            continue

        author_name = m.get("author", {}).get("name", "unknown")
        is_bot = author_name in CLARA_BOTS
        user_info = USER_REGISTRY.get(author_name, (f"unknown-{author_name}", author_name))

        kept.append({
            "content": content,
            "role": "assistant" if is_bot else "user",
            "user_id": user_info[0],
            "display_name": user_info[1],
            "timestamp": m.get("timestamp", ""),
            "source": "discord",
        })

    metadata = {
        "channel": chat.get("channel", {}).get("name", "unknown"),
        "guild": chat.get("guild", {}).get("name", "unknown"),
        "is_dm": chat.get("guild", {}).get("name", "") == "Direct Messages",
        "total": len(messages),
        "kept": len(kept),
    }

    return metadata, kept


def main():
    parser = argparse.ArgumentParser(description="Backfill verbatim chat history from Discord exports")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--file", type=str)
    args = parser.parse_args()

    from mypalclara.db import SessionLocal
    from mypalclara.db.models import Message, Session

    chats_dir = Path(__file__).resolve().parent.parent / "chats"

    if args.file:
        chat_files = [Path(args.file)]
    else:
        chat_files = sorted(chats_dir.glob("*.json"))
        chat_files = [f for f in chat_files if "_decisions" not in f.name]

    if not chat_files:
        logger.error("No chat files found")
        sys.exit(1)

    total_inserted = 0

    for chat_path in chat_files:
        metadata, messages = load_chat_with_decisions(chat_path)
        logger.info(f"Processing: {chat_path.name}")
        logger.info(f"  Channel: {metadata['channel']} ({metadata['guild']})")
        logger.info(f"  Kept: {metadata['kept']} / {metadata['total']}")

        if args.dry_run:
            if messages:
                first = messages[0]
                last = messages[-1]
                logger.info(f"  First: [{first['timestamp'][:16]}] {first['role']}: {first['content'][:60]}...")
                logger.info(f"  Last:  [{last['timestamp'][:16]}] {last['role']}: {last['content'][:60]}...")
            continue

        # Determine the primary user for this chat (for session lookup)
        human_users = [m["user_id"] for m in messages if m["role"] == "user"]
        if not human_users:
            logger.warning("  No human messages — skipping")
            continue

        from collections import Counter
        primary_user = Counter(human_users).most_common(1)[0][0]
        context_id = f"discord-{metadata['channel']}"

        db = SessionLocal()
        try:
            # Get or create session for this channel
            session = (
                db.query(Session)
                .filter(Session.user_id == primary_user, Session.context_id == context_id)
                .first()
            )

            if not session:
                from mypalclara.db.models import Project

                # Ensure default project exists
                project = db.query(Project).filter(Project.name == "Default Project").first()
                if not project:
                    import uuid
                    project = Project(
                        id=str(uuid.uuid4()),
                        owner_id=primary_user,
                        name="Default Project",
                    )
                    db.add(project)
                    db.flush()

                import uuid
                session = Session(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    user_id=primary_user,
                    context_id=context_id,
                    title=f"Discord: {metadata['channel']}",
                )
                db.add(session)
                db.flush()
                logger.info(f"  Created session: {session.id}")

            # Check what's already in the DB for this session
            existing_count = db.query(Message).filter(Message.session_id == session.id).count()
            if existing_count > 0:
                logger.info(f"  Session already has {existing_count} messages — appending new only")

                # Get the latest timestamp to avoid dupes
                latest = (
                    db.query(Message)
                    .filter(Message.session_id == session.id)
                    .order_by(Message.created_at.desc())
                    .first()
                )
                latest_ts = latest.created_at if latest else None
            else:
                latest_ts = None

            # Insert messages
            inserted = 0
            for m in messages:
                ts_str = m["timestamp"]
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo:
                        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                except (ValueError, TypeError):
                    ts = None

                # Skip if before our latest existing message
                if latest_ts and ts and ts <= latest_ts:
                    continue

                msg = Message(
                    session_id=session.id,
                    user_id=m["user_id"],
                    role=m["role"],
                    content=m["content"],
                    created_at=ts,
                )
                db.add(msg)
                inserted += 1

            db.commit()
            total_inserted += inserted
            logger.info(f"  Inserted {inserted} messages")

        finally:
            db.close()

    logger.info(f"Done: {total_inserted} messages inserted total")


if __name__ == "__main__":
    main()
