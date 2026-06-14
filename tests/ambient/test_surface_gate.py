import pytest

from mypalclara.ambient import surface_gate


def _llm(returns):
    async def fn(messages):
        return returns

    return fn


@pytest.mark.asyncio
async def test_parses_queue_decision():
    out = await surface_gate.surface_gate(
        "discord-1",
        "reflection text",
        gate_llm=_llm('{"decision": "queue", "content": "ask about the trip", "reason": "follow-up"}'),
    )
    assert out["decision"] == "queue"
    assert out["content"] == "ask about the trip"


@pytest.mark.asyncio
async def test_strips_code_fences():
    out = await surface_gate.surface_gate(
        "discord-1",
        "x",
        gate_llm=_llm('```json\n{"decision": "urgent", "content": "deadline today", "reason": "time"}\n```'),
    )
    assert out["decision"] == "urgent"
    assert out["content"] == "deadline today"


@pytest.mark.asyncio
async def test_unparseable_defaults_to_nothing():
    out = await surface_gate.surface_gate("discord-1", "x", gate_llm=_llm("I think maybe..."))
    assert out["decision"] == "nothing"


@pytest.mark.asyncio
async def test_invalid_decision_defaults_to_nothing():
    out = await surface_gate.surface_gate(
        "discord-1",
        "x",
        gate_llm=_llm('{"decision": "SHOUT", "content": "hi"}'),
    )
    assert out["decision"] == "nothing"


@pytest.mark.asyncio
async def test_llm_error_defaults_to_nothing():
    async def boom(messages):
        raise RuntimeError("llm down")

    out = await surface_gate.surface_gate("discord-1", "x", gate_llm=boom)
    assert out["decision"] == "nothing"
