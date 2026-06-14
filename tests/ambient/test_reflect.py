import pytest

from mypalclara.ambient import journal, reflect


class _Orch:
    async def generate_with_tools(self, *, messages, tools, user_id, request_id, websocket=None):
        self.seen_messages = messages
        self.seen_tools = tools
        yield {"type": "complete", "text": "Today I noticed the plan is taking shape.\n\nJournal: steady."}


class _ToolExec:
    async def get_all_tools(self, *, user_id):
        return [
            {"type": "function", "function": {"name": "search_memories"}},
            {"type": "function", "function": {"name": "send_discord_buttons"}},  # must be filtered out
            {"type": "function", "function": {"name": "add_memory"}},
        ]


@pytest.mark.asyncio
async def test_reflect_writes_journal_and_filters_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)
    # restrict allowlist to known names for the test
    monkeypatch.setattr(reflect, "REFLECTION_TOOL_ALLOWLIST", {"search_memories", "add_memory"})

    orch = _Orch()
    text = await reflect.reflect("discord-1", orchestrator=orch, tool_executor=_ToolExec())

    assert "plan is taking shape" in text
    # journal got the reflection
    assert "plan is taking shape" in journal.read_recent("discord-1", days=1)
    # destructive/irrelevant tools filtered out
    names = {t["function"]["name"] for t in orch.seen_tools}
    assert names == {"search_memories", "add_memory"}


@pytest.mark.asyncio
async def test_reflect_empty_text_skips_journal(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)

    class _Empty:
        async def generate_with_tools(self, **kw):
            yield {"type": "complete", "text": "   "}

    class _TE:
        async def get_all_tools(self, *, user_id):
            return []

    text = await reflect.reflect("discord-1", orchestrator=_Empty(), tool_executor=_TE())
    assert text.strip() == ""
    assert journal.read_recent("discord-1", days=1) == ""
