"""Collect undelivered surfaced thoughts and format them for context injection."""

from __future__ import annotations

from mypalclara.ambient import queue

_HEADER = (
    "## Thoughts from your reflection time\n\n"
    "While the user was away, you noted these to raise when you next spoke. "
    "Weave any that still fit in naturally; don't dump them as a list.\n"
)


def collect_surfaced_block(user_id: str, *, session_factory=None) -> str:
    rows = queue.fetch_undelivered(user_id, session_factory=session_factory)
    if not rows:
        return ""
    bullets = "\n".join(f"- {r.content}" for r in rows)
    queue.mark_delivered([r.id for r in rows], session_factory=session_factory)
    return f"{_HEADER}\n{bullets}"
