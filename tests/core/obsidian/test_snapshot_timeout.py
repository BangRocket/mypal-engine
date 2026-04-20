"""Tests that snapshot build + fetch are bounded by a wall-clock timeout.

Each Obsidian REST call has an httpx-level timeout (10s). But nothing today
bounds the overall snapshot build or the fetch_vault_snapshot_block wrapper.
Prompt assembly happens on every LLM turn, so a stalled Obsidian instance
must never block Clara indefinitely.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from mypalclara.core.obsidian.snapshot import VaultSnapshot, build_snapshot

pytestmark = pytest.mark.asyncio


class _StalledClient:
    """Mock client whose methods never return — simulates a fully-hung backend."""

    api_host = "h.example"

    async def list_vault(self):
        await asyncio.Event().wait()  # blocks forever

    async def list_tags(self):
        await asyncio.Event().wait()

    async def search_dql(self, *_a, **_kw):
        await asyncio.Event().wait()

    async def get_periodic(self, *_a, **_kw):
        await asyncio.Event().wait()


async def test_build_snapshot_is_bounded_by_wall_clock_timeout(monkeypatch):
    """If every subcall hangs, build_snapshot must return within its own
    wall-clock budget — NOT block Clara's turn forever."""
    # Shrink the internal budget so the test runs fast.
    import mypalclara.core.obsidian.snapshot as snap_mod
    monkeypatch.setattr(snap_mod, "SNAPSHOT_BUILD_TIMEOUT", 0.3)

    client = _StalledClient()
    try:
        # Outer safety net is strictly larger than the module's internal budget.
        snap = await asyncio.wait_for(build_snapshot(client), timeout=2.0)
    except asyncio.TimeoutError:
        pytest.fail(
            "build_snapshot did not honor its own internal timeout. "
            "Fix: wrap the inner asyncio.gather in asyncio.wait_for with a bounded "
            "timeout (and return a degraded-or-empty VaultSnapshot on timeout)."
        )
    # A fully-degraded snapshot is the right outcome here.
    assert isinstance(snap, VaultSnapshot)
    assert snap.host == "h.example"
    assert snap.top_level_folders == []
    assert snap.total_note_count == 0
    assert snap.top_tags == []
    assert snap.recent_notes == []
    assert snap.today_periodic is None


async def test_build_snapshot_total_wall_time_is_reasonable():
    """The build_snapshot total time should be close to the slowest parallel
    call, not the sum — asyncio.gather is concurrent. This catches any
    accidental serialization of the subcalls."""
    async def slow(delay):
        await asyncio.sleep(delay)

    client = AsyncMock()
    client.api_host = "h.example"
    client.list_vault = AsyncMock(side_effect=lambda: slow(0.3))
    client.list_tags = AsyncMock(side_effect=lambda: slow(0.3))
    client.search_dql = AsyncMock(side_effect=lambda *_a, **_kw: slow(0.3))
    client.get_periodic = AsyncMock(side_effect=lambda *_a, **_kw: slow(0.3))

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await build_snapshot(client)
    elapsed = loop.time() - t0

    # Four 0.3s calls in parallel should take ~0.3s, definitely not ~1.2s.
    assert elapsed < 0.9, (
        f"build_snapshot took {elapsed:.2f}s; subcalls are serialized. "
        "They should run in parallel via asyncio.gather."
    )


async def test_fetch_vault_snapshot_block_is_bounded(monkeypatch):
    """fetch_vault_snapshot_block must not hang forever even if the snapshot
    builder stalls."""
    import mypalclara.core.obsidian as obs_pkg
    from mypalclara.core.obsidian import fetch_vault_snapshot_block

    # Shrink the wrapper budget so the test runs fast.
    monkeypatch.setattr(obs_pkg, "FETCH_BLOCK_TIMEOUT", 0.3)

    class _StalledBuilderClient:
        api_host = "h.example"

    async def _stall(*_a, **_kw):
        await asyncio.Event().wait()

    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=_StalledBuilderClient()),
    ), patch(
        "mypalclara.core.obsidian._snapshot_cache.get_or_build",
        new=_stall,
    ):
        try:
            result = await asyncio.wait_for(
                fetch_vault_snapshot_block("user-1"), timeout=2.0
            )
        except asyncio.TimeoutError:
            pytest.fail(
                "fetch_vault_snapshot_block hung past its internal budget. "
                "Fix: wrap the _snapshot_cache.get_or_build call with an internal "
                "asyncio.wait_for so a broken backend can't block prompt assembly."
            )

    # On internal timeout, return None (prompt build continues without the block).
    assert result is None


async def test_fetch_vault_snapshot_block_returns_none_when_factory_fails():
    """If get_client_for_user raises (e.g., identity service down), the
    wrapper should not propagate — it should return None and let the prompt
    build continue."""
    from mypalclara.core.obsidian import fetch_vault_snapshot_block

    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(side_effect=RuntimeError("identity service unreachable")),
    ):
        result = await asyncio.wait_for(
            fetch_vault_snapshot_block("user-1"), timeout=2.0
        )
    assert result is None
