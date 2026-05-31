"""USE_PALACE_SERVICE remote-routing for emotional + topic context.

These exercise the remote branches added when wiring mypalclara to the Palace
service's emotional-context and topic-recurrence endpoints. The embedded path
is unchanged and covered elsewhere. PALACE.client (async) is bound to the
bridge loop, so the code calls ``PALACE.bridge.submit(PALACE.client.X(...))``;
the mocks below mirror that: the client method returns a sentinel "coroutine"
and bridge.submit(...) returns the resolved value.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock


def _patch_remote_palace(monkeypatch, *, submit_return):
    """Patch routed.PALACE + USE_PALACE_SERVICE for the remote path.

    Returns (palace, client, bridge) so tests can assert on calls.
    """
    from mypalclara.core.memory import routed

    client = MagicMock()
    bridge = MagicMock()
    bridge.submit = MagicMock(return_value=submit_return)
    palace = SimpleNamespace(client=client, bridge=bridge)
    monkeypatch.setattr(routed, "PALACE", palace)
    monkeypatch.setattr(routed, "USE_PALACE_SERVICE", True)
    return palace, client, bridge


def test_finalize_emotional_remote_calls_record(monkeypatch):
    import mypalclara.core.memory.context.emotional as em

    _, client, bridge = _patch_remote_palace(monkeypatch, submit_return=SimpleNamespace(emotional_arc="improving"))
    client.record_emotional_context = MagicMock(return_value="coro")

    out = em.finalize_conversation_emotional_context(
        user_id="u1",
        channel_id="c1",
        channel_name="#dm",
        is_dm=True,
        energy="focused",
        summary="job search",
        messages=["I'm frustrated", "ok", "feeling better"],
    )

    assert out is None
    client.record_emotional_context.assert_called_once()
    kwargs = client.record_emotional_context.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["messages"] == ["I'm frustrated", "ok", "feeling better"]
    assert kwargs["energy"] == "focused"
    assert kwargs["summary"] == "job search"
    bridge.submit.assert_called_once_with("coro")


def test_finalize_emotional_remote_records_with_empty_messages(monkeypatch):
    """Proactive finalize path (no messages) still records summary+energy."""
    import mypalclara.core.memory.context.emotional as em

    _, client, _ = _patch_remote_palace(monkeypatch, submit_return=SimpleNamespace(emotional_arc="stable"))
    client.record_emotional_context = MagicMock(return_value="coro")

    em.finalize_conversation_emotional_context(
        user_id="u1",
        channel_id="c1",
        channel_name="DM",
        is_dm=True,
        energy="focused",
        summary="caught up",
    )

    client.record_emotional_context.assert_called_once()
    assert client.record_emotional_context.call_args.kwargs["messages"] == []


async def test_extract_and_store_topics_remote(monkeypatch):
    import mypalclara.core.memory.context.topics as tp

    _, client, _ = _patch_remote_palace(monkeypatch, submit_return=None)
    client.extract_topics = MagicMock(return_value="coro")

    out = await tp.extract_and_store_topics(
        user_id="u1",
        channel_id="c1",
        channel_name="#dm",
        is_dm=True,
        conversation_text="x" * 60,
        conversation_sentiment=-0.2,
        llm_call=None,  # unused on the remote path
    )

    assert out == []
    client.extract_topics.assert_called_once()
    assert client.extract_topics.call_args.kwargs["conversation_text"] == "x" * 60
    assert client.extract_topics.call_args.kwargs["conversation_sentiment"] == -0.2


def test_fetch_topic_recurrence_remote(monkeypatch):
    import mypalclara.core.memory.context.topics as tp

    row = MagicMock()
    row.model_dump = MagicMock(return_value={"topic": "job search", "mention_count": 3, "pattern_note": "recurring"})
    _, client, _ = _patch_remote_palace(monkeypatch, submit_return=[row])
    client.get_topic_recurrence = MagicMock(return_value="coro")

    out = tp.fetch_topic_recurrence(user_id="u1", lookback_days=14, min_mentions=2)

    assert out == [{"topic": "job search", "mention_count": 3, "pattern_note": "recurring"}]
    client.get_topic_recurrence.assert_called_once()
    assert client.get_topic_recurrence.call_args.kwargs["user_id"] == "u1"


def test_fetch_emotional_context_remote_maps_shape(monkeypatch):
    from mypalclara.core.prompt_builder import PromptBuilder

    row = SimpleNamespace(
        emotional_arc="improving",
        energy_level="focused",
        topic_summary="job search",
        channel_name="#dm",
        is_dm=True,
        ending_sentiment=0.5,
        created_at=datetime(2026, 5, 31, tzinfo=UTC),
    )
    _, client, _ = _patch_remote_palace(monkeypatch, submit_return=[row])
    client.get_emotional_context = MagicMock(return_value="coro")

    pb = PromptBuilder(agent_id="clara")
    out = pb.fetch_emotional_context(user_id="u1", limit=3, max_age_days=7)

    assert len(out) == 1
    ctx = out[0]
    assert ctx["arc"] == "improving"
    assert ctx["energy"] == "focused"
    assert ctx["is_dm"] is True
    assert ctx["channel_name"] == "#dm"
    assert ctx["ending_sentiment"] == 0.5
    assert "job search" in ctx["memory"]
    assert ctx["timestamp"] == "2026-05-31T00:00:00+00:00"
    client.get_emotional_context.assert_called_once()
