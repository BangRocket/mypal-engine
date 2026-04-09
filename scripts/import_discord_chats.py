#!/usr/bin/env python3
"""Import Discord chat exports into Clara's memory system.

Reads Discord chat export JSON files (from DiscordChatExporter) and their
companion decision files (from the chat-cleaner tool). Only imports messages
marked as "keep". Processes conversations into episodes, entities, semantic
memories, and graph relationships.

Usage:
    # Dry run — show what would be imported
    python scripts/import_discord_chats.py --dry-run

    # Import all chats in the chats/ directory
    python scripts/import_discord_chats.py

    # Import a specific file
    python scripts/import_discord_chats.py --file "chats/Direct Messages - MyPalClara [...].json"

    # Limit session size for LLM processing
    python scripts/import_discord_chats.py --max-session-messages 40
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# User name registry — maps Discord author names to canonical names
# Extended as we discover users in the chat data
USER_REGISTRY = {
    "stairmaster401": "Josh",
    "cadacious": "cadacious",
    "MyPalClara": "Clara",
    "MyPalClarissa": "Clarissa",
}

CLARA_BOTS = {"MyPalClara", "MyPalClarissa"}


# ---------------------------------------------------------------------------
# Loading and filtering
# ---------------------------------------------------------------------------

def load_chat_with_decisions(chat_path: Path) -> tuple[dict, list[dict]]:
    """Load a chat export and filter by decisions.

    Returns (chat_metadata, kept_messages).
    """
    decisions_path = chat_path.with_name(chat_path.stem + "_decisions.json")

    with open(chat_path) as f:
        chat = json.load(f)

    decisions = {}
    if decisions_path.exists():
        with open(decisions_path) as f:
            decisions = json.load(f)
        logger.info(f"Loaded decisions from {decisions_path.name}")
    else:
        logger.warning(f"No decisions file found for {chat_path.name} — importing all messages")

    messages = chat.get("messages", [])
    kept = []
    removed = 0

    for m in messages:
        mid = m.get("id", "")
        decision = decisions.get(mid, {})
        status = decision.get("status", "keep")

        if status == "remove":
            removed += 1
            continue

        # Normalize the message
        content = m.get("content", "").strip()
        if not content:
            removed += 1
            continue

        author_name = m.get("author", {}).get("name", "unknown")
        author_id = m.get("author", {}).get("id", "")
        is_bot = m.get("author", {}).get("isBot", False)

        kept.append({
            "content": content,
            "author_name": author_name,
            "author_id": author_id,
            "canonical_name": USER_REGISTRY.get(author_name, author_name),
            "is_bot": is_bot,
            "is_clara": author_name in CLARA_BOTS,
            "role": "assistant" if author_name in CLARA_BOTS else "user",
            "timestamp": m.get("timestamp", ""),
            "message_id": mid,
        })

    metadata = {
        "channel": chat.get("channel", {}).get("name", "unknown"),
        "guild": chat.get("guild", {}).get("name", "unknown"),
        "is_dm": chat.get("guild", {}).get("name", "") == "Direct Messages",
        "total_messages": len(messages),
        "kept_messages": len(kept),
        "removed_messages": removed,
    }

    return metadata, kept


def split_into_sessions(
    messages: list[dict], gap_minutes: int = 30, max_messages: int = 40
) -> list[list[dict]]:
    """Split messages into conversation sessions based on time gaps.

    A new session starts when:
    - There's a gap of > gap_minutes between messages
    - The session exceeds max_messages (hard split)
    """
    if not messages:
        return []

    sessions = []
    current_session = [messages[0]]

    for i in range(1, len(messages)):
        prev_ts = messages[i - 1].get("timestamp", "")
        curr_ts = messages[i].get("timestamp", "")

        gap = _timestamp_gap_minutes(prev_ts, curr_ts)
        at_limit = len(current_session) >= max_messages

        if gap > gap_minutes or at_limit:
            if len(current_session) >= 2:  # Only keep sessions with 2+ messages
                sessions.append(current_session)
            current_session = []

        current_session.append(messages[i])

    if len(current_session) >= 2:
        sessions.append(current_session)

    return sessions


def _timestamp_gap_minutes(ts1: str, ts2: str) -> float:
    """Calculate gap in minutes between two ISO timestamps."""
    try:
        d1 = datetime.fromisoformat(ts1)
        d2 = datetime.fromisoformat(ts2)
        return abs((d2 - d1).total_seconds()) / 60
    except (ValueError, TypeError):
        return 0


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def format_session_for_reflection(session: list[dict]) -> list[dict]:
    """Format a session's messages for the reflection LLM call."""
    return [
        {
            "role": m["role"],
            "content": m["content"],
            "name": m["canonical_name"],
            "timestamp": m["timestamp"],
        }
        for m in session
    ]


def get_session_users(session: list[dict]) -> list[str]:
    """Get unique human (non-bot) users in a session."""
    return list({m["canonical_name"] for m in session if not m["is_clara"]})


def get_session_user_id(session: list[dict]) -> str:
    """Get the primary human user_id for a session (for memory storage).

    For DMs this is straightforward. For group chats, pick the most
    active non-bot user.
    """
    from collections import Counter

    human_msgs = [m for m in session if not m["is_clara"]]
    if not human_msgs:
        return "unknown"

    # Most active human user
    counts = Counter(m["author_id"] for m in human_msgs)
    most_active_id = counts.most_common(1)[0][0]

    # Return as discord-prefixed ID
    return f"discord-{most_active_id}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Import Discord chat exports into Clara's memory")
    parser.add_argument("--dry-run", action="store_true", help="Show stats without importing")
    parser.add_argument("--file", type=str, help="Specific chat file to import")
    parser.add_argument("--max-session-messages", type=int, default=40, help="Max messages per session (default: 40)")
    parser.add_argument("--gap-minutes", type=int, default=30, help="Gap for session splitting (default: 30)")
    args = parser.parse_args()

    from mypalclara.core.memory.config import ROOK

    if ROOK is None:
        logger.error("Rook not initialized")
        sys.exit(1)

    from mypalclara.core import make_llm
    from mypalclara.core.memory_manager import MemoryManager

    llm = make_llm()
    mm = MemoryManager.initialize(llm_callable=llm)

    if not mm.episode_store:
        logger.error("Episode store not available")
        sys.exit(1)

    # Register known users with entity resolver
    if mm.entity_resolver:
        mm.entity_resolver.register("discord-271274659385835521", "Josh", source="manual")
        mm.entity_resolver.register("discord-275457539863347201", "cadacious", source="manual")
        logger.info("Registered user name mappings")

    # Find chat files
    chats_dir = Path(__file__).resolve().parent.parent / "chats"

    if args.file:
        chat_files = [Path(args.file)]
    else:
        chat_files = sorted(chats_dir.glob("*.json"))
        # Exclude decision files
        chat_files = [f for f in chat_files if "_decisions" not in f.name]

    if not chat_files:
        logger.error("No chat files found")
        sys.exit(1)

    logger.info(f"Found {len(chat_files)} chat file(s)")

    total_sessions = 0
    total_episodes = 0
    total_errors = 0

    for chat_path in chat_files:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing: {chat_path.name}")
        logger.info(f"{'='*60}")

        metadata, messages = load_chat_with_decisions(chat_path)
        logger.info(
            f"  Channel: {metadata['channel']} ({metadata['guild']})"
            f"  {'DM' if metadata['is_dm'] else 'Group'}"
        )
        logger.info(
            f"  Total: {metadata['total_messages']} | "
            f"Kept: {metadata['kept_messages']} | "
            f"Removed: {metadata['removed_messages']}"
        )

        sessions = split_into_sessions(
            messages,
            gap_minutes=args.gap_minutes,
            max_messages=args.max_session_messages,
        )
        logger.info(f"  Split into {len(sessions)} sessions")

        if args.dry_run:
            for i, session in enumerate(sessions[:5]):
                users = get_session_users(session)
                ts_start = session[0].get("timestamp", "")[:16]
                ts_end = session[-1].get("timestamp", "")[:16]
                logger.info(
                    f"    Session {i+1}: {len(session)} msgs, "
                    f"users={users}, {ts_start} → {ts_end}"
                )
            if len(sessions) > 5:
                logger.info(f"    ... and {len(sessions) - 5} more sessions")
            continue

        # Process each session
        start = time.time()

        for i, session in enumerate(sessions):
            user_id = get_session_user_id(session)
            users = get_session_users(session)
            ts_start = session[0].get("timestamp", "")[:16]

            logger.info(
                f"  [{i+1}/{len(sessions)}] Session: {len(session)} msgs, "
                f"users={users}, start={ts_start}"
            )

            try:
                msg_dicts = format_session_for_reflection(session)
                result = mm.reflect_on_session(
                    msg_dicts, user_id, session_id=metadata["channel"]
                )

                if result:
                    ep_count = len(result.get("episodes", []))
                    ent_count = len(result.get("entities", []))
                    note_count = len(result.get("self_notes", []))
                    total_episodes += ep_count
                    logger.info(f"    → {ep_count} episodes, {ent_count} entities, {note_count} self-notes")
                else:
                    logger.info("    → No reflection output")

            except Exception as e:
                logger.error(f"    → Failed: {e}")
                total_errors += 1

            total_sessions += 1

            # Progress
            if (i + 1) % 10 == 0:
                elapsed = time.time() - start
                rate = (i + 1) / elapsed if elapsed > 0 else 0
                logger.info(f"    Progress: {i+1}/{len(sessions)} ({rate:.1f} sessions/s)")

    if not args.dry_run:
        logger.info(f"\n{'='*60}")
        logger.info(
            f"Import complete: {total_episodes} episodes from {total_sessions} sessions "
            f"({total_errors} errors)"
        )


if __name__ == "__main__":
    main()
