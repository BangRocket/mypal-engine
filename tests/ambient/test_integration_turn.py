from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mypalclara.ambient import inject, loop, queue
from mypalclara.db.models import Base


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/e2e.db")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


@pytest.mark.asyncio
async def test_queue_decision_lands_and_injects(tmp_path, monkeypatch):
    sf = _factory(tmp_path)
    monkeypatch.setattr(loop, "recently_active", lambda *a, **k: False)

    async def reflect(uid, **kw):
        return "I keep coming back to the migration plan."

    async def gate(uid, refl, **kw):
        return {"decision": "queue", "content": "follow up on the migration plan", "reason": "thread"}

    async def send_fn(uid, ch, content):
        raise AssertionError("queue decision must not DM")

    def enqueue(uid, content, **kw):
        queue.enqueue(uid, content, kind=kw.get("kind", "queue"), expires_at=kw.get("expires_at"), session_factory=sf)

    await loop.ambient_turn(
        "discord-1",
        orchestrator=None,
        tool_executor=None,
        gate_llm=None,
        send_fn=send_fn,
        now_utc=datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc),
        _reflect=reflect,
        _gate=gate,
        _enqueue=enqueue,
        _user_ctx=lambda u, **k: loop.UserCtx(timezone="America/New_York"),
    )

    block = inject.collect_surfaced_block("discord-1", session_factory=sf)
    assert "follow up on the migration plan" in block
