"""Tests that PromptBuilder injects a vault snapshot block when provided."""
from __future__ import annotations

from mypalclara.core.llm.messages import SystemMessage
from mypalclara.core.prompt_builder import PromptBuilder, PromptMode


def _system_text(messages):
    return "\n".join(m.content for m in messages if isinstance(m, SystemMessage))


def test_minimal_mode_no_vault_block():
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt(
        user_mems=[], proj_mems=[], thread_summary=None, recent_msgs=[],
        user_message="hi", mode=PromptMode.MINIMAL,
    )
    assert "User Context" not in _system_text(messages)


def test_minimal_mode_with_vault_block_injects():
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt(
        user_mems=[], proj_mems=[], thread_summary=None, recent_msgs=[],
        user_message="hi", mode=PromptMode.MINIMAL,
        vault_snapshot_block="Vault has 100 notes across Projects/, Daily/.",
    )
    text = _system_text(messages)
    assert "User Context" in text
    assert "100 notes" in text


def test_full_mode_with_vault_block_injects():
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt(
        user_mems=[], proj_mems=[], thread_summary=None, recent_msgs=[],
        user_message="hi",
        # default mode is FULL
        vault_snapshot_block="Recent edits: a.md, b.md.",
    )
    text = _system_text(messages)
    assert "User Context" in text
    assert "a.md" in text


def test_none_mode_ignores_vault_block():
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt(
        user_mems=[], proj_mems=[], thread_summary=None, recent_msgs=[],
        user_message="hi", mode=PromptMode.NONE,
        vault_snapshot_block="this should be ignored",
    )
    text = _system_text(messages)
    assert "User Context" not in text
    assert "this should be ignored" not in text


def test_vault_block_appears_after_persona():
    """Ordering: persona system msg comes first, then the vault block."""
    builder = PromptBuilder(agent_id="t")
    messages = builder.build_prompt(
        user_mems=[], proj_mems=[], thread_summary=None, recent_msgs=[],
        user_message="hi", mode=PromptMode.MINIMAL,
        vault_snapshot_block="Vault summary.",
    )
    # The first SystemMessage should be the persona (includes "Security Instructions"
    # from worm_persona); the vault block should be in a LATER SystemMessage.
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    assert len(system_msgs) >= 2
    assert "Security Instructions" in system_msgs[0].content
    assert any("User Context" in m.content for m in system_msgs[1:])
