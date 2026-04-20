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
        "obsidian_search",
        "obsidian_query",
    }
    assert expected <= names  # E2+E3 ship these; later tasks add more


def test_system_prompt_mentions_key_tools():
    # The prompt references the canonical tool names so Clara's downstream
    # guidance doesn't drift from the actual tool surface.
    assert "obsidian_search" in SYSTEM_PROMPT
    assert "obsidian_get_file" in SYSTEM_PROMPT
    assert "obsidian_patch_file" in SYSTEM_PROMPT
    assert "obsidian_append_to_periodic_note" in SYSTEM_PROMPT
    assert "obsidian_open_file" in SYSTEM_PROMPT


def test_system_prompt_frames_vault_as_shared_memory():
    """The prompt must position Obsidian as durable/shared memory, not just
    raw vault access. Drift here is worth failing a test over — the whole
    point is for Clara to treat the vault as a memory layer."""
    lower = SYSTEM_PROMPT.lower()
    assert "memory" in lower, "SYSTEM_PROMPT should frame the vault as memory"
    # Must cover both directions of the memory loop.
    assert "read" in lower
    assert "writ" in lower  # matches "write" / "writing" / "writes"
    # Explicit distinction from the internal Palace so Clara doesn't
    # conflate the two.
    assert "palace" in lower


def test_system_prompt_warns_against_writing_sensitive_data():
    """Regression guard: Clara should not write tokens/secrets to the vault
    as part of her 'remember this' behavior."""
    lower = SYSTEM_PROMPT.lower()
    assert "token" in lower or "password" in lower or "sensitive" in lower, (
        "SYSTEM_PROMPT should discourage writing credentials to the vault."
    )


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
        if t.intent == "read":
            assert t.risk_level == "safe"


# ---- E3 handler tests ----


async def test_search_requires_query():
    from mypalclara.core.core_tools.obsidian_tool import _handle_search

    result = await _handle_search({}, _ctx())
    assert "'query' is required" in result


async def test_search_rejects_negative_context_length():
    from mypalclara.core.core_tools.obsidian_tool import _handle_search

    result = await _handle_search({"query": "x", "context_length": -1}, _ctx())
    assert "non-negative" in result


async def test_search_rejects_bad_context_length_type():
    from mypalclara.core.core_tools.obsidian_tool import _handle_search

    result = await _handle_search({"query": "x", "context_length": "lots"}, _ctx())
    assert "must be an integer" in result


async def test_search_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_search

    hits = [{"filename": "a.md", "matches": ["hit"]}]
    client = _mock_client(search_simple=hits)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_search({"query": "clara"}, _ctx())
    import json

    assert json.loads(result) == hits
    client.search_simple.assert_awaited_once_with("clara", context_length=None)


async def test_search_passes_context_length():
    from mypalclara.core.core_tools.obsidian_tool import _handle_search

    client = _mock_client(search_simple=[])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        await _handle_search({"query": "clara", "context_length": 200}, _ctx())
    client.search_simple.assert_awaited_once_with("clara", context_length=200)


async def test_query_rejects_unknown_type():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    result = await _handle_query({"query_type": "sql", "query": "x"}, _ctx())
    assert "'query_type' must be 'dql' or 'jsonlogic'" in result


async def test_query_dql_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    results = [{"path": "a.md"}]
    client = _mock_client(search_dql=results)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_query({"query_type": "dql", "query": 'TABLE file.mtime FROM "" LIMIT 5'}, _ctx())
    import json

    assert json.loads(result) == results
    client.search_dql.assert_awaited_once()


async def test_query_dql_rejects_non_string():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    result = await _handle_query({"query_type": "dql", "query": {"not": "string"}}, _ctx())
    assert "must be a string" in result


async def test_query_jsonlogic_with_dict():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    results = [{"path": "a.md"}]
    client = _mock_client(search_jsonlogic=results)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_query(
            {"query_type": "jsonlogic", "query": {"in": ["clara", {"var": "tags"}]}},
            _ctx(),
        )
    import json

    assert json.loads(result) == results


async def test_query_jsonlogic_with_json_string():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    client = _mock_client(search_jsonlogic=[])
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        await _handle_query({"query_type": "jsonlogic", "query": '{"var": "file.tags"}'}, _ctx())
    client.search_jsonlogic.assert_awaited_once_with({"var": "file.tags"})


async def test_query_jsonlogic_rejects_malformed_json_string():
    from mypalclara.core.core_tools.obsidian_tool import _handle_query

    result = await _handle_query({"query_type": "jsonlogic", "query": "not json"}, _ctx())
    assert "JSON object or JSON string" in result


def test_all_nine_read_plus_search_tools_registered():
    names = {t.name for t in TOOLS}
    assert "obsidian_search" in names
    assert "obsidian_query" in names
    assert len(names) >= 9


# ---- E4 handler tests ----

from unittest.mock import MagicMock


@pytest.fixture
def mock_snapshot_cache(monkeypatch):
    """Replace the module-level _snapshot_cache with a MagicMock to observe invalidate calls."""
    import mypalclara.core.core_tools.obsidian_tool as tool_mod

    fake = MagicMock()
    monkeypatch.setattr(tool_mod, "_snapshot_cache", fake)
    return fake


async def test_create_or_update_file_happy_path(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_create_or_update_file

    client = _mock_client(put_file=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_create_or_update_file({"path": "new.md", "content": "hi"}, _ctx("u1"))
    assert "Wrote new.md" in result
    client.put_file.assert_awaited_once_with("new.md", "hi")
    mock_snapshot_cache.invalidate.assert_called_once_with("u1")


async def test_create_or_update_does_not_invalidate_on_error(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_create_or_update_file
    from mypalclara.core.obsidian.exceptions import ObsidianAuthError

    client = _mock_client(put_file=ObsidianAuthError("401"))
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_create_or_update_file({"path": "x.md", "content": "y"}, _ctx("u1"))
    assert "authentication failed" in result.lower()
    mock_snapshot_cache.invalidate.assert_not_called()


async def test_create_or_update_file_requires_path(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_create_or_update_file

    result = await _handle_create_or_update_file({"content": "x"}, _ctx())
    assert "'path' is required" in result
    mock_snapshot_cache.invalidate.assert_not_called()


async def test_append_to_file_happy_path(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_append_to_file

    client = _mock_client(append_file=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_append_to_file({"path": "note.md", "content": "- new\n"}, _ctx("u2"))
    assert "Appended to note.md" in result
    client.append_file.assert_awaited_once_with("note.md", "- new\n")
    mock_snapshot_cache.invalidate.assert_called_once_with("u2")


async def test_patch_file_happy_path(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_patch_file

    client = _mock_client(patch_file=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_patch_file(
            {"path": "note.md", "target_type": "heading", "target": "## Log", "content": "- entry\n"}, _ctx("u1")
        )
    assert "Patched note.md" in result
    client.patch_file.assert_awaited_once_with("note.md", "heading", "## Log", "- entry\n", operation="append")
    mock_snapshot_cache.invalidate.assert_called_once_with("u1")


async def test_patch_file_rejects_bad_target_type(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_patch_file

    result = await _handle_patch_file(
        {"path": "x.md", "target_type": "section", "target": "t", "content": "c"},
        _ctx(),
    )
    assert "target_type" in result and "heading, block, frontmatter" in result
    mock_snapshot_cache.invalidate.assert_not_called()


async def test_patch_file_rejects_bad_operation(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_patch_file

    result = await _handle_patch_file(
        {"path": "x.md", "target_type": "heading", "target": "H", "content": "c", "operation": "overwrite"},
        _ctx(),
    )
    assert "operation" in result and "append, prepend, replace" in result
    mock_snapshot_cache.invalidate.assert_not_called()


async def test_append_to_periodic_note_happy_path(mock_snapshot_cache):
    from datetime import date as _date

    from mypalclara.core.core_tools.obsidian_tool import _handle_append_to_periodic_note

    client = _mock_client(append_periodic=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_append_to_periodic_note(
            {"period": "daily", "content": "- marker\n", "date": "2026-04-20"},
            _ctx("u1"),
        )
    assert "Appended to daily note" in result
    client.append_periodic.assert_awaited_once_with("daily", "- marker\n", _date(2026, 4, 20))
    mock_snapshot_cache.invalidate.assert_called_once_with("u1")


async def test_append_to_periodic_note_requires_content(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_append_to_periodic_note

    result = await _handle_append_to_periodic_note({"period": "daily"}, _ctx())
    assert "'content' is required" in result
    mock_snapshot_cache.invalidate.assert_not_called()


async def test_delete_file_happy_path(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_delete_file

    client = _mock_client(delete_file=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_delete_file({"path": "trash.md"}, _ctx("u9"))
    assert "Deleted trash.md" in result
    client.delete_file.assert_awaited_once_with("trash.md")
    mock_snapshot_cache.invalidate.assert_called_once_with("u9")


async def test_delete_file_not_found(mock_snapshot_cache):
    from mypalclara.core.core_tools.obsidian_tool import _handle_delete_file
    from mypalclara.core.obsidian.exceptions import ObsidianNotFoundError

    client = _mock_client(delete_file=ObsidianNotFoundError("gone"))
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_delete_file({"path": "gone.md"}, _ctx("u9"))
    assert "Note not found: gone.md" in result
    mock_snapshot_cache.invalidate.assert_not_called()


def test_all_fourteen_tools_are_present_so_far():
    names = {t.name for t in TOOLS}
    # E2 (7) + E3 (2) + E4 (5) = 14 write-capable surface; E5 adds 2 more UI tools.
    assert len(names) >= 14
    expected_writes = {
        "obsidian_create_or_update_file",
        "obsidian_append_to_file",
        "obsidian_patch_file",
        "obsidian_append_to_periodic_note",
        "obsidian_delete_file",
    }
    assert expected_writes <= names


def test_write_tools_have_correct_risk_and_intent():
    write_names = {
        "obsidian_create_or_update_file",
        "obsidian_append_to_file",
        "obsidian_patch_file",
        "obsidian_append_to_periodic_note",
        "obsidian_delete_file",
    }
    for t in TOOLS:
        if t.name in write_names:
            assert t.intent == "write"
            if t.name == "obsidian_delete_file":
                assert t.risk_level == "dangerous"
            else:
                assert t.risk_level == "moderate"


# ---- E5 handler tests ----


async def test_open_file_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_open_file

    client = _mock_client(open_file=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_open_file({"path": "Projects/note.md"}, _ctx())
    assert "Opened Projects/note.md" in result
    client.open_file.assert_awaited_once_with("Projects/note.md")


async def test_open_file_requires_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_open_file

    result = await _handle_open_file({}, _ctx())
    assert "'path' is required" in result


async def test_execute_command_happy_path():
    from mypalclara.core.core_tools.obsidian_tool import _handle_execute_command

    client = _mock_client(execute_command=None)
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_execute_command(
            {"command_id": "editor:save-file"}, _ctx()
        )
    assert "Executed Obsidian command: editor:save-file" in result
    client.execute_command.assert_awaited_once_with("editor:save-file")


async def test_execute_command_requires_id():
    from mypalclara.core.core_tools.obsidian_tool import _handle_execute_command

    result = await _handle_execute_command({}, _ctx())
    assert "'command_id' is required" in result


async def test_execute_command_not_found():
    from mypalclara.core.core_tools.obsidian_tool import _handle_execute_command
    from mypalclara.core.obsidian.exceptions import ObsidianNotFoundError

    client = _mock_client(execute_command=ObsidianNotFoundError("no such command"))
    with patch(
        "mypalclara.core.core_tools.obsidian_tool.get_client_for_user",
        new=AsyncMock(return_value=client),
    ):
        result = await _handle_execute_command(
            {"command_id": "nonexistent"}, _ctx()
        )
    assert "Resource not found" in result or "Not found" in result or "not found" in result.lower()


def test_all_sixteen_tools_registered_in_module():
    names = {t.name for t in TOOLS}
    assert len(names) == 16, f"Expected 16 tools, got {len(names)}: {sorted(names)}"
    expected = {
        # Read (7)
        "obsidian_list_vault",
        "obsidian_list_dir",
        "obsidian_get_file",
        "obsidian_get_active_file",
        "obsidian_get_periodic_note",
        "obsidian_list_tags",
        "obsidian_list_commands",
        # Search (2)
        "obsidian_search",
        "obsidian_query",
        # Write (5)
        "obsidian_create_or_update_file",
        "obsidian_append_to_file",
        "obsidian_patch_file",
        "obsidian_append_to_periodic_note",
        "obsidian_delete_file",
        # UI / commands (2)
        "obsidian_open_file",
        "obsidian_execute_command",
    }
    assert names == expected
