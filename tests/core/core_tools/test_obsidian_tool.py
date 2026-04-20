"""Tests for the Obsidian tool module (E1 scaffold + E2 read tools)."""

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
from mypalclara.tools._base import ToolContext

pytestmark = pytest.mark.asyncio


def test_module_metadata():
    assert MODULE_NAME == "obsidian"
    assert MODULE_VERSION == "1.0.0"
    assert isinstance(SYSTEM_PROMPT, str)
    assert len(SYSTEM_PROMPT) > 100  # non-trivial guidance block


def test_tools_list_contains_expected_read_tools():
    names = {t.name for t in TOOLS}
    expected = {
        "obsidian_list_vault",
        "obsidian_list_dir",
        "obsidian_get_file",
        "obsidian_get_active_file",
        "obsidian_get_periodic_note",
        "obsidian_list_tags",
        "obsidian_list_commands",
    }
    assert expected <= names  # E2 ships these; later tasks add more


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


# ---- E2 handler tests ----


def _ctx(user_id: str = "u1") -> ToolContext:
    return ToolContext(user_id=user_id)


def _mock_client(**methods):
    client = AsyncMock()
    client.api_host = "h.example"
    for name, value in methods.items():
        if isinstance(value, Exception):
            setattr(client, name, AsyncMock(side_effect=value))
        else:
            setattr(client, name, AsyncMock(return_value=value))
    return client


async def test_list_vault_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_vault

    client = _mock_client(list_vault=["a.md", "Projects/"])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_list_vault({}, _ctx())
    import json

    assert json.loads(result) == ["a.md", "Projects/"]


async def test_list_vault_not_configured():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_vault

    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=None),
    ):
        result = await _handle_list_vault({}, _ctx())
    assert "not configured" in result.lower()


async def test_list_dir_requires_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_dir

    result = await _handle_list_dir({}, _ctx())
    assert "'path' is required" in result


async def test_list_dir_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_dir

    client = _mock_client(list_dir=["a.md", "nested/"])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_list_dir({"path": "Projects"}, _ctx())
    import json

    assert json.loads(result) == ["a.md", "nested/"]


async def test_get_file_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_file

    client = _mock_client(get_file="# Title\n\nBody.")
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_get_file({"path": "note.md"}, _ctx())
    assert result == "# Title\n\nBody."


async def test_get_file_not_found_returns_user_message():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_file
    from mypalclara.core.obsidian.exceptions import ObsidianNotFoundError

    client = _mock_client(get_file=ObsidianNotFoundError("404"))
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_get_file({"path": "missing.md"}, _ctx())
    assert "Note not found: missing.md" in result


async def test_get_active_file_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_active_file

    client = _mock_client(get_active="Active body.")
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_get_active_file({}, _ctx())
    assert result == "Active body."


async def test_get_periodic_note_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_periodic_note

    client = _mock_client(get_periodic="# 2026-04-20\n")
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_get_periodic_note({"period": "daily"}, _ctx())
    assert "2026-04-20" in result


async def test_get_periodic_note_rejects_invalid_period():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_periodic_note

    result = await _handle_get_periodic_note({"period": "hourly"}, _ctx())
    assert "must be one of" in result


async def test_get_periodic_note_rejects_bad_date():
    from mypalclara.core.core_tools.obsidian_tool import _handle_get_periodic_note

    result = await _handle_get_periodic_note({"period": "daily", "date": "not-a-date"}, _ctx())
    assert "YYYY-MM-DD" in result


async def test_get_periodic_note_with_date():
    from datetime import date as _date

    from mypalclara.core.core_tools.obsidian_tool import _handle_get_periodic_note

    client = _mock_client(get_periodic="content")
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        await _handle_get_periodic_note({"period": "daily", "date": "2026-04-20"}, _ctx())
    client.get_periodic.assert_awaited_once_with("daily", _date(2026, 4, 20))


async def test_list_tags_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_tags

    client = _mock_client(list_tags=[("work", 42), ("clara", 17)])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_list_tags({}, _ctx())
    import json

    parsed = json.loads(result)
    assert parsed == [{"tag": "work", "count": 42}, {"tag": "clara", "count": 17}]


async def test_list_commands_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_commands

    client = _mock_client(list_commands=[{"id": "app:reload", "name": "Reload"}])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_list_commands({}, _ctx())
    import json

    assert json.loads(result) == [{"id": "app:reload", "name": "Reload"}]


async def test_auth_error_returns_user_message():
    from mypalclara.core.core_tools.obsidian_tool import _handle_list_vault
    from mypalclara.core.obsidian.exceptions import ObsidianAuthError

    client = _mock_client(list_vault=ObsidianAuthError("401"))
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_list_vault({}, _ctx())
    assert "authentication failed" in result.lower()


def test_all_seven_read_tools_have_availability():
    for t in TOOLS:
        assert t.availability is has_obsidian_config
        assert t.intent == "read"
        assert t.risk_level == "safe"
