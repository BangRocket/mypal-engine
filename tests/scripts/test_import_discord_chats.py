"""Unit tests for the service-aware Discord importer."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import import_discord_chats as importer  # noqa: E402


def _msg(author_name, *, is_clara, content="hi", ts="2025-12-20T12:00:00"):
    return {
        "content": content,
        "author_name": author_name,
        "author_id": f"id-{author_name}",
        "canonical_name": author_name,
        "is_bot": is_clara,
        "is_clara": is_clara,
        "role": "assistant" if is_clara else "user",
        "timestamp": ts,
        "message_id": "m1",
    }


def test_resolve_channel_agent_clara_dm():
    msgs = [_msg("Josh", is_clara=False), _msg("MyPalClara", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clara"


def test_resolve_channel_agent_clarissa():
    msgs = [_msg("Josh", is_clara=False), _msg("MyPalClarissa", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clarissa"


def test_resolve_channel_agent_dominant_wins():
    msgs = [_msg("MyPalClarissa", is_clara=True)] * 3 + [_msg("MyPalClara", is_clara=True)]
    assert importer.resolve_channel_agent(msgs, default_agent="x") == "clarissa"


def test_resolve_channel_agent_fallback_when_no_bot():
    msgs = [_msg("Josh", is_clara=False), _msg("cadacious", is_clara=False)]
    assert importer.resolve_channel_agent(msgs, default_agent="fallback") == "fallback"


def _session():
    return [_msg("Josh", is_clara=False, content="hello"),
            _msg("MyPalClara", is_clara=True, content="hi Josh")]


def test_import_session_service_uses_sync_and_agent():
    session = _session()
    palace = MagicMock()
    palace.bridge.submit.return_value = ["ep1", "ep2"]  # stand-in episodes
    mm = MagicMock()

    result = importer.import_session(
        session, user_id="discord-1", agent_id="clarissa",
        channel="ask-clarissa", mm=mm, palace=palace, use_service=True,
    )

    # client.reflect_session called once with mode=sync + the per-channel agent
    palace.client.reflect_session.assert_called_once_with(
        messages=importer.format_session_for_reflection(session),
        user_id="discord-1",
        agent_id="clarissa",
        session_id="ask-clarissa",
        mode="sync",
    )
    # the coroutine was dispatched on the bridge
    palace.bridge.submit.assert_called_once()
    assert result == {"episodes": ["ep1", "ep2"]}
    # embedded path must NOT be used in service mode
    mm.reflect_on_session.assert_not_called()


def test_import_session_embedded_uses_manager():
    session = _session()
    mm = MagicMock()
    mm.reflect_on_session.return_value = {"episodes": [1], "entities": [], "self_notes": []}
    palace = MagicMock()

    result = importer.import_session(
        session, user_id="discord-1", agent_id="clara",
        channel="MyPalClara", mm=mm, palace=palace, use_service=False,
    )

    mm.reflect_on_session.assert_called_once_with(
        importer.format_session_for_reflection(session),
        "discord-1",
        session_id="MyPalClara",
    )
    assert result == {"episodes": [1], "entities": [], "self_notes": []}
    palace.client.reflect_session.assert_not_called()


def test_maybe_reset_noop_when_not_requested():
    mm, palace = MagicMock(), MagicMock()
    assert importer.maybe_reset(False, False, mm, palace) is False


def test_maybe_reset_skips_in_service_mode():
    mm, palace = MagicMock(), MagicMock()
    # Even with do_reset=True, service mode must not touch any store.
    assert importer.maybe_reset(True, True, mm, palace) is False
    palace.delete_all.assert_not_called()
    mm.episode_store.assert_not_called()


def test_maybe_reset_runs_embedded(monkeypatch):
    called = {}
    monkeypatch.setattr(importer, "reset_embedded_memory",
                        lambda mm, palace: called.setdefault("ran", True))
    assert importer.maybe_reset(True, False, MagicMock(), MagicMock()) is True
    assert called.get("ran") is True
