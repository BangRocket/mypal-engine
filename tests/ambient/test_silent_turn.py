import pytest

from mypalclara.ambient.silent_turn import run_silent_turn


class _FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def generate_with_tools(self, *, messages, tools, user_id, request_id, websocket=None):
        self.calls.append({"websocket": websocket, "tools": tools})
        yield {"type": "chunk", "text": "partial..."}
        yield {"type": "tool_start", "tool_name": "search_memory"}
        yield {"type": "tool_result", "tool_name": "search_memory", "success": True}
        yield {"type": "complete", "text": "final reflection text", "tool_count": 1}


@pytest.mark.asyncio
async def test_returns_complete_text_and_passes_no_websocket():
    orch = _FakeOrchestrator()
    out = await run_silent_turn(orch, [{"role": "user", "content": "x"}], [], "discord-1", "req-1")
    assert out == "final reflection text"
    assert orch.calls[0]["websocket"] is None  # silent: nothing forwarded to an adapter
