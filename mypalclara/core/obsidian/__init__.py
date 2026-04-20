"""Obsidian Local REST API integration (client, factory, snapshot, cache)."""
from __future__ import annotations

import asyncio
import logging

from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.snapshot import build_snapshot

logger = logging.getLogger("clara.obsidian")

# Process-lifetime singleton. Callers that need an isolated cache (mainly
# tests) should instantiate SnapshotCache directly.
_snapshot_cache = SnapshotCache(builder=build_snapshot)

# Upper bound on fetch_vault_snapshot_block. Must be ≥ SNAPSHOT_BUILD_TIMEOUT
# and strictly less than the gateway's prompt-assembly budget. Covers the
# identity-service credentials lookup + snapshot build + cache bookkeeping.
FETCH_BLOCK_TIMEOUT: float = 8.0


async def fetch_vault_snapshot_block(canonical_user_id: str) -> str | None:
    """Return a prompt-ready snapshot block for the user, or None if unconfigured.

    Looks up the user's Obsidian credentials via the identity service, builds
    (or cache-hits) the snapshot, and renders it via to_prompt_block(). If
    the user has no Obsidian configuration, returns None. Never blocks prompt
    assembly indefinitely — any failure or timeout returns None.
    """
    try:
        return await asyncio.wait_for(
            _fetch_snapshot_inner(canonical_user_id),
            timeout=FETCH_BLOCK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "fetch_vault_snapshot_block exceeded %.1fs budget for user=%s; "
            "skipping snapshot injection for this turn",
            FETCH_BLOCK_TIMEOUT,
            canonical_user_id,
        )
        return None
    except Exception as e:
        logger.warning(
            "fetch_vault_snapshot_block failed for user=%s: %s",
            canonical_user_id,
            e,
        )
        return None


async def _fetch_snapshot_inner(canonical_user_id: str) -> str | None:
    from mypalclara.core.obsidian.factory import get_client_for_user

    client = await get_client_for_user(canonical_user_id)
    if client is None:
        return None
    snap = await _snapshot_cache.get_or_build(canonical_user_id, client)
    return snap.to_prompt_block()


__all__ = ["_snapshot_cache", "fetch_vault_snapshot_block", "FETCH_BLOCK_TIMEOUT"]
