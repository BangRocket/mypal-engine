"""In-memory snapshot cache keyed by canonical_user_id.

Cache entries have no base TTL — they live until explicitly invalidated
(via `invalidate(user_id)`) or until the process restarts. Build failures
store a short-lived `unavailable` sentinel instead so Obsidian being down
doesn't trigger a hot loop of failed builds.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from mypalclara.core.obsidian.snapshot import VaultSnapshot

logger = logging.getLogger("clara.obsidian.cache")


class SnapshotCache:
    """Per-user snapshot cache with failure-TTL sentinel.

    Parameters
    ----------
    builder:
        Async callable `(client) -> VaultSnapshot`. Typically
        `mypalclara.core.obsidian.snapshot.build_snapshot`.
    failure_ttl:
        Seconds to cache the `unavailable` sentinel before retrying.
        Default 30s — short enough that transient outages recover quickly,
        long enough to avoid hammering a downed Obsidian instance.
    """

    def __init__(
        self,
        builder: Callable[[object], Awaitable[VaultSnapshot]],
        failure_ttl: float = 30.0,
    ) -> None:
        self._builder = builder
        self._failure_ttl = failure_ttl
        # Map user_id -> (expires_at_monotonic, snapshot). expires_at is
        # float("inf") for healthy cached snapshots and a monotonic time
        # for the failure sentinel.
        self._store: dict[str, tuple[float, VaultSnapshot]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        lock = self._locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[user_id] = lock
        return lock

    def invalidate(self, user_id: str) -> None:
        """Drop the cached snapshot for a user.

        Typical callers: obsidian_* write tools after a successful mutation,
        and the identity service's DELETE /users/me/obsidian-config flow
        (via a webhook, when implemented).
        """
        self._store.pop(user_id, None)

    async def get_or_build(self, user_id: str, client: object) -> VaultSnapshot:
        """Return the cached snapshot for a user, building one if missing.

        The build is serialized per-user via an asyncio.Lock so concurrent
        LLM turns for the same user don't trigger parallel builds.
        """
        entry = self._store.get(user_id)
        if entry is not None:
            expires_at, snap = entry
            if snap.unavailable and time.monotonic() >= expires_at:
                # failure sentinel expired — fall through to rebuild
                self._store.pop(user_id, None)
            else:
                return snap

        async with self._lock_for(user_id):
            # Re-check under lock — another coroutine may have populated it.
            entry = self._store.get(user_id)
            if entry is not None:
                expires_at, snap = entry
                if not (snap.unavailable and time.monotonic() >= expires_at):
                    return snap
                self._store.pop(user_id, None)

            try:
                snap = await self._builder(client)
                self._store[user_id] = (float("inf"), snap)
                return snap
            except Exception:
                logger.warning(
                    "snapshot build failed for user %s; caching unavailable sentinel",
                    user_id,
                    exc_info=True,
                )
                host = getattr(client, "api_host", "?")
                sentinel = VaultSnapshot(host=host, unavailable=True)
                self._store[user_id] = (
                    time.monotonic() + self._failure_ttl,
                    sentinel,
                )
                return sentinel
