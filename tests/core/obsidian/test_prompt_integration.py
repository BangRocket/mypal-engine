"""End-to-end integration: identity service → factory → client → snapshot →
prompt block. Uses mocked HTTP at the edges only, exercises the real
SnapshotCache / build_snapshot / to_prompt_block / PromptBuilder code paths.

Guards against regressions like:
- Snapshot block text not actually reaching the system prompt.
- Factory/cache coordination breaking when wired together.
- User-configured users silently getting None blocks because of mismatched
  canonical_user_id threading.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mypalclara.core.llm.messages import SystemMessage
from mypalclara.core.obsidian import fetch_vault_snapshot_block
from mypalclara.core.obsidian.cache import SnapshotCache
from mypalclara.core.obsidian.factory import clear_client_cache
from mypalclara.core.prompt_builder import PromptBuilder

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolate_caches(monkeypatch):
    """Ensure each test gets fresh Obsidian caches (client + snapshot)."""
    import mypalclara.core.obsidian as obs_pkg
    from mypalclara.core.obsidian.snapshot import build_snapshot

    # Fresh snapshot cache per test so one test's sentinel doesn't leak.
    fresh_cache = SnapshotCache(builder=build_snapshot)
    monkeypatch.setattr(obs_pkg, "_snapshot_cache", fresh_cache)
    clear_client_cache()
    yield
    clear_client_cache()


def _mock_obsidian_client(**methods) -> MagicMock:
    client = MagicMock()
    client.api_host = methods.get("api_host", "obsidian.shmp.app")
    for name in (
        "list_vault", "list_tags", "search_dql", "get_periodic",
        "list_dir", "get_file", "get_active",
    ):
        default = methods.get(name, [])
        client_method = AsyncMock(return_value=default)
        setattr(client, name, client_method)
    return client


async def test_fetch_block_returns_formatted_block_for_configured_user(monkeypatch):
    """Happy path: configured user with a working vault gets a rendered block."""
    client = _mock_obsidian_client(
        list_vault=["Projects/", "Daily/", "a.md", "b.md"],
        list_tags=[("work", 42), ("clara", 17)],
        search_dql=[{"path": "Projects/foo.md"}, {"path": "Daily/today.md"}],
        get_periodic="# 2026-04-20\n\nBody.",
    )
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        block = await fetch_vault_snapshot_block("user-1")

    assert block is not None
    assert "obsidian.shmp.app" in block
    assert "Projects" in block or "Daily" in block
    assert "#work" in block or "#clara" in block
    assert "Projects/foo.md" in block or "Daily/today.md" in block


async def test_fetch_block_returns_none_when_factory_returns_none():
    """Unconfigured user: factory returns None, block is None."""
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=None),
    ):
        result = await fetch_vault_snapshot_block("unconfigured-user")
    assert result is None


async def test_fetch_block_returns_none_when_factory_raises():
    """Identity service unreachable: factory raises; we swallow and return None."""
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(side_effect=RuntimeError("identity service 503")),
    ):
        result = await fetch_vault_snapshot_block("u1")
    assert result is None


async def test_fetch_block_caches_across_calls(monkeypatch):
    """Second fetch within the session hits the snapshot cache (no new build)."""
    build_count = {"n": 0}

    async def counting_builder(client):
        build_count["n"] += 1
        from mypalclara.core.obsidian.snapshot import VaultSnapshot
        return VaultSnapshot(host=client.api_host, total_note_count=1)

    import mypalclara.core.obsidian as obs_pkg
    monkeypatch.setattr(obs_pkg, "_snapshot_cache", SnapshotCache(builder=counting_builder))

    client = _mock_obsidian_client()
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        await fetch_vault_snapshot_block("u1")
        await fetch_vault_snapshot_block("u1")

    assert build_count["n"] == 1


async def test_prompt_block_ends_up_in_system_messages():
    """End-to-end: fetched block lands as a system message in build_prompt output."""
    client = _mock_obsidian_client(
        list_vault=["Projects/"],
        list_tags=[("work", 10)],
        search_dql=[{"path": "a.md"}],
        get_periodic=None,
    )
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        block = await fetch_vault_snapshot_block("u1")

    assert block is not None

    builder = PromptBuilder(agent_id="t")
    from mypalclara.core.prompt_builder import PromptMode
    messages = builder.build_prompt(
        user_mems=[],
        proj_mems=[],
        thread_summary=None,
        recent_msgs=[],
        user_message="tell me about my vault",
        mode=PromptMode.MINIMAL,
        user_id="u1",
        vault_snapshot_block=block,
    )

    system_text = "\n".join(
        m.content for m in messages if isinstance(m, SystemMessage)
    )
    assert "User Context" in system_text
    assert "obsidian.shmp.app" in system_text


async def test_block_remains_stable_with_unicode_and_markdown_noise():
    """Snapshot rendering must not break when vault contents contain unicode,
    backticks, or other characters that could otherwise break downstream
    string formatting."""
    client = _mock_obsidian_client(
        list_vault=["Projets / 2026/", "日本語/", "Backtick`Note.md"],
        list_tags=[("工作", 5), ("déjà-vu", 3)],
        search_dql=[{"path": "日本語/notes.md"}, {"path": "project`name.md"}],
        get_periodic="## Entry\n\nLine with `inline code` and émoji 🔧.",
    )
    with patch(
        "mypalclara.core.obsidian.factory.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        block = await fetch_vault_snapshot_block("u1")

    assert block is not None
    # Reasonable bounds — the renderer caps to a single prompt chunk.
    assert len(block) < 2000
    # Block rendered without throwing on any non-ASCII input.
    assert "obsidian.shmp.app" in block
