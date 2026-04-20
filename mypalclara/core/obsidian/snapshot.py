"""Vault snapshot — a lightweight summary of the user's Obsidian vault
that gets injected into Clara's system prompt.

Structure:
- top-level folders + approximate note count
- top tags by usage
- 5 most recently-modified notes (requires Dataview plugin for DQL query)
- today's daily note title/excerpt (if it exists)

The snapshot is built via parallel REST calls with per-call timeouts, and
degrades gracefully: if any single call fails, that field gets an empty
default rather than failing the whole snapshot. If ALL calls fail, the
caller wraps the result with the `unavailable` flag (handled in D4's cache,
not here).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypalclara.core.obsidian.client import ObsidianClient

logger = logging.getLogger("clara.obsidian.snapshot")


@dataclass
class VaultSnapshot:
    """Lightweight snapshot of a user's Obsidian vault for prompt injection."""

    host: str
    top_level_folders: list[str] = field(default_factory=list)
    total_note_count: int = 0
    top_tags: list[tuple[str, int]] = field(default_factory=list)
    recent_notes: list[str] = field(default_factory=list)
    today_periodic: str | None = None
    fetched_at: datetime = field(default_factory=datetime.utcnow)
    unavailable: bool = False
    """When True, snapshot build failed entirely; the prompt block should
    note that vault details are temporarily unavailable."""

    def to_prompt_block(self) -> str:
        """Render the snapshot as a concise prompt block (~200 tokens)."""
        if self.unavailable:
            return (
                "User has Obsidian configured but vault details are currently "
                "unavailable."
            )
        folders = ", ".join(self.top_level_folders[:10]) or "(none)"
        tags = (
            ", ".join(f"#{name} ({count})" for name, count in self.top_tags[:8])
            or "(none)"
        )
        recent = ", ".join(self.recent_notes[:5]) or "(none)"
        periodic = self.today_periodic or "none yet"
        return (
            f"**User's Obsidian vault** ({self.host}): "
            f"{self.total_note_count} notes across folders: {folders}. "
            f"Recent edits: {recent}. "
            f"Today's daily note: {periodic}. "
            f"Top tags: {tags}."
        )


async def _safe_call(coro, default):
    """Await a coroutine; on any exception, log and return the default."""
    try:
        return await coro
    except Exception:
        logger.debug("snapshot subcall failed", exc_info=True)
        return default


async def build_snapshot(client: ObsidianClient) -> VaultSnapshot:
    """Assemble a VaultSnapshot from the user's Obsidian vault.

    Issues four parallel calls against the client and aggregates the
    results. Sub-call failures degrade that field only; the overall
    snapshot always succeeds (in the worst case, all fields are empty
    and the caller decides whether to treat that as "unavailable").
    """
    async def _folders_and_count():
        listing = await client.list_vault()
        folders = [p.rstrip("/") for p in listing if p.endswith("/")]
        count = sum(1 for p in listing if not p.endswith("/"))
        return folders, count

    async def _top_tags():
        tags = await client.list_tags()
        return tags[:10]

    async def _recent_notes():
        hits = await client.search_dql(
            'TABLE file.mtime FROM "" SORT file.mtime DESC LIMIT 5'
        )
        return [h.get("path", "") for h in (hits or []) if h.get("path")]

    async def _today_periodic():
        try:
            content = await client.get_periodic("daily")
        except Exception:
            return None
        if not content:
            return None
        # First non-empty line, stripped of leading '#' chars, capped at 80 chars
        for line in content.splitlines():
            line = line.strip()
            if line:
                return line.lstrip("#").strip()[:80]
        return None

    folders_count, tags, recent, periodic = await asyncio.gather(
        _safe_call(_folders_and_count(), ([], 0)),
        _safe_call(_top_tags(), []),
        _safe_call(_recent_notes(), []),
        _safe_call(_today_periodic(), None),
    )
    folders, count = folders_count
    return VaultSnapshot(
        host=client.api_host,
        top_level_folders=folders,
        total_note_count=count,
        top_tags=tags,
        recent_notes=recent,
        today_periodic=periodic,
    )
