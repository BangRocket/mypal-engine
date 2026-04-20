"""Tests for SnapshotCache — per-user snapshot caching with failure sentinel."""
from __future__ import annotations

import asyncio

import pytest

from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.snapshot import VaultSnapshot

pytestmark = pytest.mark.asyncio


class _FakeClient:
    def __init__(self, host: str = "h.example") -> None:
        self.api_host = host


# ---- basic hit/miss ----

async def test_cache_miss_invokes_builder():
    calls = {"n": 0}

    async def builder(client):
        calls["n"] += 1
        return VaultSnapshot(host=client.api_host, total_note_count=7)

    cache = SnapshotCache(builder=builder)
    snap = await cache.get_or_build("user-1", _FakeClient())
    assert snap.total_note_count == 7
    assert calls["n"] == 1


async def test_cache_hit_returns_same_instance():
    async def builder(client):
        return VaultSnapshot(host=client.api_host)

    cache = SnapshotCache(builder=builder)
    client = _FakeClient()
    s1 = await cache.get_or_build("u1", client)
    s2 = await cache.get_or_build("u1", client)
    assert s1 is s2


async def test_cache_does_not_expire_healthy_snapshot():
    """Healthy snapshots live indefinitely until invalidated."""
    async def builder(client):
        return VaultSnapshot(host=client.api_host)

    cache = SnapshotCache(builder=builder, failure_ttl=0.01)
    c = _FakeClient()
    s1 = await cache.get_or_build("u1", c)
    # Wait longer than failure_ttl; healthy entry is not supposed to expire.
    await asyncio.sleep(0.02)
    s2 = await cache.get_or_build("u1", c)
    assert s1 is s2


# ---- per-user isolation ----

async def test_different_users_get_different_snapshots():
    async def builder(client):
        return VaultSnapshot(host=client.api_host)

    cache = SnapshotCache(builder=builder)
    s_alice = await cache.get_or_build("alice", _FakeClient("ha"))
    s_bob = await cache.get_or_build("bob", _FakeClient("hb"))
    assert s_alice is not s_bob
    assert s_alice.host == "ha"
    assert s_bob.host == "hb"


# ---- invalidate ----

async def test_invalidate_forces_rebuild_on_next_get():
    count = {"n": 0}

    async def builder(client):
        count["n"] += 1
        return VaultSnapshot(host=client.api_host, total_note_count=count["n"])

    cache = SnapshotCache(builder=builder)
    c = _FakeClient()
    s1 = await cache.get_or_build("u1", c)
    cache.invalidate("u1")
    s2 = await cache.get_or_build("u1", c)
    assert s1 is not s2
    assert s1.total_note_count == 1
    assert s2.total_note_count == 2


async def test_invalidate_noop_on_missing_user():
    cache = SnapshotCache(builder=None)  # builder unused
    cache.invalidate("never-cached")  # should not raise


# ---- concurrency lock ----

async def test_concurrent_builds_serialize_via_lock():
    """Two concurrent get_or_build for the same user run the builder ONCE."""
    call_count = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def builder(client):
        call_count["n"] += 1
        started.set()
        await release.wait()
        return VaultSnapshot(host=client.api_host, total_note_count=1)

    cache = SnapshotCache(builder=builder)
    c = _FakeClient()

    t1 = asyncio.create_task(cache.get_or_build("u1", c))
    await started.wait()
    # Second task starts while first is blocked in the builder
    t2 = asyncio.create_task(cache.get_or_build("u1", c))
    await asyncio.sleep(0.01)  # give t2 a chance to hit the lock
    release.set()

    s1, s2 = await asyncio.gather(t1, t2)
    assert s1 is s2
    assert call_count["n"] == 1


# ---- failure sentinel ----

async def test_builder_raises_caches_unavailable_sentinel():
    async def builder(client):
        raise RuntimeError("obsidian is down")

    cache = SnapshotCache(builder=builder, failure_ttl=10.0)
    snap = await cache.get_or_build("u1", _FakeClient("h.example"))
    assert snap.unavailable is True
    assert snap.host == "h.example"


async def test_sentinel_served_from_cache_within_ttl():
    calls = {"n": 0}

    async def builder(client):
        calls["n"] += 1
        raise RuntimeError("down")

    cache = SnapshotCache(builder=builder, failure_ttl=10.0)
    c = _FakeClient()
    s1 = await cache.get_or_build("u1", c)
    s2 = await cache.get_or_build("u1", c)
    assert s1 is s2
    assert calls["n"] == 1  # not retried


async def test_sentinel_expires_after_failure_ttl():
    calls = {"n": 0}

    async def builder(client):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")
        return VaultSnapshot(host=client.api_host, total_note_count=99)

    cache = SnapshotCache(builder=builder, failure_ttl=0.02)
    c = _FakeClient()
    s1 = await cache.get_or_build("u1", c)
    assert s1.unavailable is True

    await asyncio.sleep(0.03)

    s2 = await cache.get_or_build("u1", c)
    assert s2.unavailable is False
    assert s2.total_note_count == 99
    assert calls["n"] == 2


async def test_invalidate_clears_failure_sentinel_too():
    async def builder(client):
        raise RuntimeError("down")

    cache = SnapshotCache(builder=builder, failure_ttl=10.0)
    c = _FakeClient()
    await cache.get_or_build("u1", c)

    cache.invalidate("u1")
    # After invalidate, next call re-runs builder (which still fails,
    # producing a fresh sentinel)
    snap = await cache.get_or_build("u1", c)
    assert snap.unavailable is True
