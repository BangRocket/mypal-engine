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
            {"type": "function", "function": {"name": "search_chat_history"}},
            {"type": "function", "function": {"name": "terminal"}},  # must be filtered out
            {"type": "function", "function": {"name": "get_chat_history"}},
        ]


class _MM:
    def __init__(self):
        self.calls = []

    def add_to_memory(self, *, user_id, user_message, assistant_reply, is_dm=False):
        self.calls.append({"user_id": user_id, "assistant_reply": assistant_reply})


@pytest.mark.asyncio
async def test_reflect_writes_journal_filters_tools_and_consolidates(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)

    orch = _Orch()
    mm = _MM()
    text = await reflect.reflect("discord-1", orchestrator=orch, tool_executor=_ToolExec(), memory_manager=mm)

    assert "plan is taking shape" in text
    assert "plan is taking shape" in journal.read_recent("discord-1", days=1)
    names = {t["function"]["name"] for t in orch.seen_tools}
    assert names == {"search_chat_history", "get_chat_history"}  # destructive tool filtered out
    assert len(mm.calls) == 1
    assert mm.calls[0]["user_id"] == "discord-1"
    assert "plan is taking shape" in mm.calls[0]["assistant_reply"]


@pytest.mark.asyncio
async def test_reflect_empty_text_skips_journal_and_memory(tmp_path, monkeypatch):
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

    mm = _MM()
    text = await reflect.reflect("discord-1", orchestrator=_Empty(), tool_executor=_TE(), memory_manager=mm)
    assert text.strip() == ""
    assert journal.read_recent("discord-1", days=1) == ""
    assert mm.calls == []


@pytest.mark.asyncio
async def test_reflect_palace_write_failure_is_nonfatal(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENT_JOURNAL_DIR", str(tmp_path))
    import importlib
    importlib.reload(journal)
    importlib.reload(reflect)

    class _BadMM:
        def add_to_memory(self, **kw):
            raise RuntimeError("palace down")

    text = await reflect.reflect("discord-1", orchestrator=_Orch(), tool_executor=_ToolExec(), memory_manager=_BadMM())
    assert "plan is taking shape" in text
    assert "plan is taking shape" in journal.read_recent("discord-1", days=1)
