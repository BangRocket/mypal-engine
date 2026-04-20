"""Live integration tests against obsidian.shmp.app.

These tests are gated on the OBSIDIAN_DEV_TOKEN env var and tagged with the
`integration` marker. They are SKIPPED by default. To run them:

    OBSIDIAN_DEV_TOKEN=... poetry run pytest tests/integration/test_obsidian_live.py -m integration -v

The tests create a temporary marker note in the vault and clean up after
themselves. They are idempotent under partial failure.
"""
from __future__ import annotations

import os
import uuid

import pytest

from mypalclara.core.obsidian.client import ObsidianClient
from mypalclara.core.obsidian.exceptions import ObsidianError, ObsidianNotFoundError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _dev_token() -> str | None:
    return os.environ.get("OBSIDIAN_DEV_TOKEN")


def _dev_host() -> str:
    return os.environ.get("OBSIDIAN_DEV_HOST", "obsidian.shmp.app")


skip_reason = "OBSIDIAN_DEV_TOKEN not set; skipping live Obsidian integration tests."


@pytest.fixture
def live_client() -> ObsidianClient:
    token = _dev_token()
    if not token:
        pytest.skip(skip_reason)
    return ObsidianClient(_dev_host(), token, verify_tls=True)


@pytest.fixture
def marker_path():
    """A unique vault path so parallel test runs don't collide."""
    return f"clara-integration-test/{uuid.uuid4()}.md"


async def test_live_server_reachable(live_client):
    """Smoke: list_vault succeeds, proves auth + TLS + DNS all work."""
    files = await live_client.list_vault()
    assert isinstance(files, list)


async def test_live_search_simple_runs(live_client):
    hits = await live_client.search_simple("clara")
    assert isinstance(hits, list)


async def test_live_list_tags_runs(live_client):
    """Tags endpoint returns a list of (tag, count) tuples."""
    tags = await live_client.list_tags()
    assert isinstance(tags, list)
    for entry in tags:
        assert isinstance(entry, tuple)
        assert len(entry) == 2


async def test_live_periodic_today_either_exists_or_404(live_client):
    """Today's daily note might exist or not; either is a valid production
    state. Assert only that the client doesn't crash in either case."""
    try:
        content = await live_client.get_periodic("daily")
        assert isinstance(content, str)
    except ObsidianNotFoundError:
        pass


async def test_live_put_append_get_delete_roundtrip(live_client, marker_path):
    """End-to-end write/read/delete. Cleans up on success and failure."""
    try:
        # Create the note
        await live_client.put_file(marker_path, "# Integration marker\n\nInitial body.\n")
        content = await live_client.get_file(marker_path)
        assert "# Integration marker" in content
        assert "Initial body." in content

        # Append content
        await live_client.append_file(marker_path, "\nAdded by test.\n")
        content = await live_client.get_file(marker_path)
        assert "Added by test." in content
    finally:
        # Always try to clean up, even if the assertions above failed
        try:
            await live_client.delete_file(marker_path)
        except ObsidianError:
            pass


async def test_live_list_dir_works_for_existing_dir(live_client):
    """Pick ANY directory from list_vault, then list_dir it."""
    listing = await live_client.list_vault()
    dirs = [p for p in listing if p.endswith("/")]
    if not dirs:
        pytest.skip("Vault has no top-level directories to enumerate.")
    sub = await live_client.list_dir(dirs[0].rstrip("/"))
    assert isinstance(sub, list)


async def test_live_build_snapshot_succeeds(live_client):
    """The snapshot builder aggregates list_vault / list_tags / search_dql /
    get_periodic. At least one of those should return non-empty for a
    non-empty vault. Partial failures are acceptable (e.g., if DQL isn't
    enabled), but the snapshot itself should not raise."""
    from mypalclara.core.obsidian.snapshot import build_snapshot

    snap = await build_snapshot(live_client)
    assert snap.host == _dev_host()
    assert snap.unavailable is False  # build_snapshot always returns a sentinel-free snapshot
    # We don't assert on the vault contents — any shape is valid for a real vault.
    prompt_block = snap.to_prompt_block()
    assert "Obsidian vault" in prompt_block
