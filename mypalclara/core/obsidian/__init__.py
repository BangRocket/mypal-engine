"""Obsidian Local REST API integration (client, factory, snapshot, cache)."""
from __future__ import annotations

from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.snapshot import build_snapshot

# Process-lifetime singleton. Callers that need an isolated cache (mainly
# tests) should instantiate SnapshotCache directly.
_snapshot_cache = SnapshotCache(builder=build_snapshot)


async def fetch_vault_snapshot_block(canonical_user_id: str) -> str | None:
    """Return a prompt-ready snapshot block for the user, or None if unconfigured.

    Looks up the user's Obsidian credentials via the identity service, builds
    (or cache-hits) the snapshot, and renders it via to_prompt_block(). If
    the user has no Obsidian configuration, returns None.
    """
    from mypalclara.core.obsidian.factory import get_client_for_user

    client = await get_client_for_user(canonical_user_id)
    if client is None:
        return None
    snap = await _snapshot_cache.get_or_build(canonical_user_id, client)
    return snap.to_prompt_block()


__all__ = ["_snapshot_cache", "fetch_vault_snapshot_block"]
