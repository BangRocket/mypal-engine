from datetime import datetime, timezone

import pytest

from mypalclara.ambient import loop


class _Deps:
    """Bundle of injected fakes for ambient_turn."""

    def __init__(self, gate_decision):
        self.sent = []
        self.enqueued = []
        self._gate = gate_decision

        async def reflect(user_id, **kw):
            return "reflection text"

        async def gate(user_id, reflection, **kw):
            return self._gate

        async def send_fn(user_id, channel_id, content):
            self.sent.append((user_id, channel_id, content))
            return True

        def enqueue(user_id, content, **kw):
            self.enqueued.append((user_id, content, kw.get("kind")))

        self.reflect = reflect
        self.gate = gate
        self.send_fn = send_fn
        self.enqueue = enqueue


def _cfg(tz="America/New_York", last_dm=None):
    return loop.UserCtx(timezone=tz, last_dm_at=last_dm)


@pytest.mark.asyncio
async def test_nothing_decision_sends_and_queues_nothing(monkeypatch):
    d = _Deps({"decision": "nothing", "content": "", "reason": ""})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == [] and d.enqueued == []


@pytest.mark.asyncio
async def test_queue_decision_enqueues(monkeypatch):
    d = _Deps({"decision": "queue", "content": "mention the trip", "reason": "x"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.enqueued and d.enqueued[0][2] == "queue"
    assert d.sent == []


@pytest.mark.asyncio
async def test_urgent_in_hours_sends_dm(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "deadline today!", "reason": "time"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    recorded = []
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),  # 08:00 NY
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
        _record_dm=lambda u, t, **k: recorded.append(u),
    )
    assert d.sent == [("discord-1", "dm-discord-1", "deadline today!")]
    assert recorded == ["discord-1"]


@pytest.mark.asyncio
async def test_urgent_outside_hours_downgrades_to_queue(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "late thought", "reason": "x"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 2, 0, tzinfo=timezone.utc),  # 22:00 NY
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == []
    assert d.enqueued and d.enqueued[0][2] == "queue"


@pytest.mark.asyncio
async def test_recent_activity_skips_everything(monkeypatch):
    d = _Deps({"decision": "urgent", "content": "x", "reason": "y"})
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: True)  # active → skip
    await loop.ambient_turn(
        "discord-1", orchestrator=None, tool_executor=None, gate_llm=None,
        send_fn=d.send_fn, now_utc=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
        _reflect=d.reflect, _gate=d.gate, _enqueue=d.enqueue, _user_ctx=lambda u, **k: _cfg(),
    )
    assert d.sent == [] and d.enqueued == []
