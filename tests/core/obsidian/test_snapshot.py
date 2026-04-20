"""Tests for VaultSnapshot + build_snapshot."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mypalclara.core.obsidian.snapshot import VaultSnapshot, build_snapshot

pytestmark = pytest.mark.asyncio


def _mock_client(**methods) -> AsyncMock:
    """Helper: build an AsyncMock with the given coroutine return values."""
    client = AsyncMock()
    client.api_host = methods.get("api_host", "h.example")
    for name, value in methods.items():
        if name == "api_host":
            continue
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return client


# ---- VaultSnapshot.to_prompt_block ----

def test_to_prompt_block_happy_path():
    snap = VaultSnapshot(
        host="h.example",
        top_level_folders=["Projects", "Daily", "Reference"],
        total_note_count=1247,
        top_tags=[("work", 412), ("clara", 187)],
        recent_notes=["Projects/a.md", "Daily/b.md"],
        today_periodic="2026-04-20",
    )
    text = snap.to_prompt_block()
    assert "h.example" in text
    assert "1247" in text
    assert "Projects" in text
    assert "#work (412)" in text
    assert "Projects/a.md" in text
    assert "2026-04-20" in text


def test_to_prompt_block_unavailable_sentinel():
    snap = VaultSnapshot(host="h", unavailable=True)
    text = snap.to_prompt_block()
    assert "unavailable" in text.lower()
    assert "h" not in text or "unavailable" in text


def test_to_prompt_block_empty_fields():
    snap = VaultSnapshot(host="h")
    text = snap.to_prompt_block()
    assert "h" in text
    assert "(none)" in text
    assert "none yet" in text


def test_to_prompt_block_truncates_long_lists():
    """Top tags list should clip at 8 entries, folders at 10, recent at 5."""
    snap = VaultSnapshot(
        host="h",
        top_level_folders=[f"f{i}" for i in range(20)],
        top_tags=[(f"t{i}", 100 - i) for i in range(20)],
        recent_notes=[f"n{i}.md" for i in range(20)],
    )
    text = snap.to_prompt_block()
    assert "f0" in text and "f9" in text  # first 10 folders
    assert "f10" not in text
    assert "t0" in text and "t7" in text  # first 8 tags
    assert "t8" not in text
    assert "n0.md" in text and "n4.md" in text  # first 5 recent
    assert "n5.md" not in text


# ---- build_snapshot ----

async def test_build_snapshot_all_sources_succeed():
    client = _mock_client(
        list_vault=["Projects/", "Daily/", "a.md", "b.md", "c.md"],
        list_tags=[("work", 42), ("clara", 17)],
        search_dql=[{"path": "a.md"}, {"path": "b.md"}],
        get_periodic="# 2026-04-20\n\nBody text.",
    )
    snap = await build_snapshot(client)

    assert snap.host == "h.example"
    assert snap.top_level_folders == ["Projects", "Daily"]
    assert snap.total_note_count == 3
    assert snap.top_tags == [("work", 42), ("clara", 17)]
    assert snap.recent_notes == ["a.md", "b.md"]
    assert snap.today_periodic == "2026-04-20"
    assert snap.unavailable is False


async def test_build_snapshot_dql_failure_degrades_recent():
    client = _mock_client(
        list_vault=["Projects/"],
        list_tags=[("x", 1)],
        search_dql=RuntimeError("dataview plugin not installed"),
        get_periodic="# Today\n",
    )
    snap = await build_snapshot(client)
    assert snap.top_level_folders == ["Projects"]
    assert snap.top_tags == [("x", 1)]
    assert snap.recent_notes == []  # degraded
    assert snap.today_periodic == "Today"


async def test_build_snapshot_periodic_404_returns_none():
    from mypalclara.core.obsidian.exceptions import ObsidianNotFoundError
    client = _mock_client(
        list_vault=[],
        list_tags=[],
        search_dql=[],
        get_periodic=ObsidianNotFoundError("no daily note"),
    )
    snap = await build_snapshot(client)
    assert snap.today_periodic is None


async def test_build_snapshot_all_subcalls_fail():
    """If every subcall fails, snapshot is still returned (empty, not unavailable)."""
    client = _mock_client(
        list_vault=RuntimeError("nope"),
        list_tags=RuntimeError("nope"),
        search_dql=RuntimeError("nope"),
        get_periodic=RuntimeError("nope"),
    )
    snap = await build_snapshot(client)
    assert snap.top_level_folders == []
    assert snap.total_note_count == 0
    assert snap.top_tags == []
    assert snap.recent_notes == []
    assert snap.today_periodic is None
    assert snap.unavailable is False  # unavailable is a CACHE-LEVEL decision (D4)


async def test_build_snapshot_tags_limited_to_10():
    """If the client returns 20 tags, the snapshot keeps 10."""
    many_tags = [(f"t{i}", 100 - i) for i in range(20)]
    client = _mock_client(
        list_vault=[],
        list_tags=many_tags,
        search_dql=[],
        get_periodic=None,
    )
    snap = await build_snapshot(client)
    assert len(snap.top_tags) == 10
    assert snap.top_tags[0] == ("t0", 100)


async def test_build_snapshot_folders_filtered_by_trailing_slash():
    client = _mock_client(
        list_vault=["a.md", "Projects/", "b.md", "Daily/", "c.md"],
        list_tags=[],
        search_dql=[],
        get_periodic=None,
    )
    snap = await build_snapshot(client)
    assert snap.top_level_folders == ["Projects", "Daily"]
    assert snap.total_note_count == 3


async def test_build_snapshot_empty_recent_notes_filtered():
    """DQL hits without a 'path' field should be dropped."""
    client = _mock_client(
        list_vault=[],
        list_tags=[],
        search_dql=[{"path": "a.md"}, {"mtime": "2026"}, {"path": ""}, {"path": "b.md"}],
        get_periodic=None,
    )
    snap = await build_snapshot(client)
    assert snap.recent_notes == ["a.md", "b.md"]


async def test_build_snapshot_periodic_extracts_first_nonempty_line():
    client = _mock_client(
        list_vault=[],
        list_tags=[],
        search_dql=[],
        get_periodic="\n\n## Agenda\n\n- item 1\n",
    )
    snap = await build_snapshot(client)
    assert snap.today_periodic == "Agenda"
