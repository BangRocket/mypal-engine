"""Smoke test that the module-level _snapshot_cache is wired correctly."""
import pytest

from mypalclara.core.obsidian import _snapshot_cache
from mypalclara.core.obsidian.cache import SnapshotCache


def test_snapshot_cache_singleton_exists():
    assert isinstance(_snapshot_cache, SnapshotCache)


def test_fetch_vault_snapshot_block_returns_none_when_unconfigured(monkeypatch):
    """If the user has no Obsidian config, the helper returns None (no exception)."""
    import asyncio
    from unittest.mock import AsyncMock
    from mypalclara.core.obsidian import fetch_vault_snapshot_block
    from mypalclara.core.obsidian import factory

    monkeypatch.setattr(factory, "get_client_for_user", AsyncMock(return_value=None))
    result = asyncio.run(fetch_vault_snapshot_block("any-user"))
    assert result is None
