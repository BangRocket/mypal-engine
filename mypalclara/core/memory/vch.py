"""Verbatim Chat History (VCH) search.

Full-text search over the raw messages table in PostgreSQL.
Returns actual conversation exchanges, not derived summaries.
Used as an L2 retrieval source alongside episodes and semantic memories.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger("clara.memory.vch")


def search_vch(
    query: str,
    user_id: str,
    limit: int = 5,
    context_window: int = 2,
) -> list[dict[str, Any]]:
    """Search verbatim chat history via PostgreSQL full-text search.

    Args:
        query: The search query (natural language).
        user_id: The user whose conversations to search.
        limit: Max number of matching messages to return.
        context_window: Number of surrounding messages to include
            before/after each match (for conversational context).

    Returns:
        List of conversation snippets, each containing the matched
        message plus surrounding context. Format:
        [
            {
                "messages": [
                    {"role": "user", "content": "...", "timestamp": "..."},
                    {"role": "assistant", "content": "...", "timestamp": "..."},  # match
                    {"role": "user", "content": "...", "timestamp": "..."},
                ],
                "matched_content": "the specific matched message",
                "rank": 0.95,
                "timestamp": "2026-01-15T10:30:00",
            }
        ]
    """
    try:
        from sqlalchemy import text as sql_text

        from mypalclara.db import SessionLocal

        db = SessionLocal()
        try:
            # Find matching messages with full-text search
            # Uses PostgreSQL's ts_vector/ts_query for relevance ranking
            matches = db.execute(
                sql_text("""
                    SELECT m.id, m.session_id, m.content, m.role, m.created_at,
                           ts_rank(to_tsvector('english', m.content), plainto_tsquery('english', :query)) AS rank
                    FROM messages m
                    JOIN sessions s ON s.id = m.session_id
                    WHERE s.user_id = :user_id
                      AND to_tsvector('english', m.content) @@ plainto_tsquery('english', :query)
                      AND length(m.content) > 20
                    ORDER BY rank DESC, m.created_at DESC
                    LIMIT :limit
                """),
                {"query": query, "user_id": user_id, "limit": limit},
            ).fetchall()

            if not matches:
                return []

            # For each match, fetch surrounding messages for context
            snippets = []
            seen_sessions = set()

            for match in matches:
                msg_id, session_id, content, role, created_at, rank = match

                # Avoid duplicate snippets from the same session region
                session_key = f"{session_id}:{created_at}"
                if session_key in seen_sessions:
                    continue
                seen_sessions.add(session_key)

                # Fetch surrounding messages
                context_msgs = db.execute(
                    sql_text("""
                        SELECT role, content, created_at, user_id
                        FROM messages
                        WHERE session_id = :session_id
                          AND created_at BETWEEN
                              (SELECT created_at - interval '5 minutes' FROM messages WHERE id = :msg_id)
                              AND
                              (SELECT created_at + interval '5 minutes' FROM messages WHERE id = :msg_id)
                        ORDER BY created_at ASC
                        LIMIT :max_context
                    """),
                    {"session_id": session_id, "msg_id": msg_id, "max_context": context_window * 2 + 1},
                ).fetchall()

                messages_list = []
                for ctx_role, ctx_content, ctx_ts, ctx_uid in context_msgs:
                    messages_list.append({
                        "role": ctx_role,
                        "content": ctx_content,
                        "timestamp": ctx_ts.isoformat() if isinstance(ctx_ts, datetime) else str(ctx_ts or ""),
                    })

                snippets.append({
                    "messages": messages_list,
                    "matched_content": content,
                    "rank": float(rank),
                    "timestamp": created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or ""),
                })

            logger.debug(f"VCH search: {len(snippets)} snippets for '{query[:50]}...'")
            return snippets

        finally:
            db.close()

    except Exception as e:
        logger.warning(f"VCH search failed: {e}")
        return []


def format_vch_for_context(snippets: list[dict], max_chars: int = 2000) -> str:
    """Format VCH search results for inclusion in the LLM context.

    Args:
        snippets: Results from search_vch().
        max_chars: Budget for VCH context.

    Returns:
        Formatted string for the prompt.
    """
    if not snippets:
        return ""

    parts = []
    chars_used = 0

    for snippet in snippets:
        ts = snippet.get("timestamp", "")[:10]
        lines = []
        for msg in snippet["messages"]:
            name = "Clara" if msg["role"] == "assistant" else "User"
            lines.append(f"  {name}: {msg['content']}")

        block = f"[{ts}]\n" + "\n".join(lines)

        if chars_used + len(block) > max_chars:
            break
        parts.append(block)
        chars_used += len(block)

    return "\n\n".join(parts)
