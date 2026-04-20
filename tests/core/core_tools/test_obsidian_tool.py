"""Tests for the Obsidian tool module scaffold (E1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from mypalclara.core.core_tools.obsidian_tool import (
    MODULE_NAME,
    MODULE_VERSION,
    SYSTEM_PROMPT,
    TOOLS,
    has_obsidian_config,
)

pytestmark = pytest.mark.asyncio


def test_module_metadata():
    assert MODULE_NAME == "obsidian"
    assert MODULE_VERSION == "1.0.0"
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 100  # non-trivial guidance block


def test_tools_list_initially_empty():
    """E1 scaffolds an empty TOOLS list; E2-E5 populate it."""
    # Each of E2 (7), E3 (2), E4 (5), E5 (2) will add tools; at E1 it is empty.
    assert TOOLS == []


def test_system_prompt_mentions_key_tools():
    # The prompt references the canonical tool names so Clara's downstream
    # guidance doesn't drift from the actual tool surface.
    assert "obsidian_search" in SYSTEM_PROMPT
    assert "obsidian_get_file" in SYSTEM_PROMPT
    assert "obsidian_patch_file" in SYSTEM_PROMPT
    assert "obsidian_append_to_periodic_note" in SYSTEM_PROMPT
    assert "obsidian_open_file" in SYSTEM_PROMPT


async def test_has_obsidian_config_true_when_client_returned():
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=object()),
    ):
        assert await has_obsidian_config("user-1") is True


async def test_has_obsidian_config_false_when_none():
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=None),
    ):
        assert await has_obsidian_config("user-1") is False


async def test_has_obsidian_config_false_on_exception():
    """A failing factory (e.g. identity service down) should not raise;
    the predicate returns False so the tools simply disappear from the
    user's inventory for that request."""
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(side_effect=RuntimeError("identity service down")),
    ):
        assert await has_obsidian_config("user-1") is False
