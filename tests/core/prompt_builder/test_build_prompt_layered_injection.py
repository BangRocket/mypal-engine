"""Mirror of D5 tests, but for build_prompt_layered."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mypalclara.core.llm.messages import SystemMessage
from mypalclara.core.prompt_builder import PromptBuilder


def _system_text(messages):
    return "\n".join(m.content for m in messages if isinstance(m, SystemMessage))


@pytest.fixture
def mock_memory_manager():
    """Minimal stand-in so build_prompt_layered can run without Palace/graph."""
    mm = MagicMock()
    mm.fetch_user_profile.return_value = None
    mm.fetch_active_arcs.return_value = []
    return mm


def test_layered_no_vault_block(mock_memory_manager):
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt_layered(
        user_id="u1",
        user_message="hi",
        recent_msgs=[],
        memory_manager=mock_memory_manager,
    )
    assert "User Context" not in _system_text(messages)


def test_layered_with_vault_block_injects(mock_memory_manager):
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt_layered(
        user_id="u1",
        user_message="hi",
        recent_msgs=[],
        memory_manager=mock_memory_manager,
        vault_snapshot_block="Vault has 100 notes.",
    )
    text = _system_text(messages)
    assert "User Context" in text
    assert "100 notes" in text


def test_layered_with_system_prompts(mock_memory_manager):
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt_layered(
        user_id="u1",
        user_message="hi",
        recent_msgs=[],
        memory_manager=mock_memory_manager,
        system_prompts=[("obsidian", "Prefer search before get_file.")],
    )
    text = _system_text(messages)
    assert "Tool-specific guidance — obsidian" in text


def test_layered_vault_block_appears_after_persona(mock_memory_manager):
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt_layered(
        user_id="u1",
        user_message="hi",
        recent_msgs=[],
        memory_manager=mock_memory_manager,
        vault_snapshot_block="Summary.",
    )
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    assert len(system_msgs) >= 2
    assert "Security Instructions" in system_msgs[0].content
    assert any("User Context" in m.content for m in system_msgs[1:])
