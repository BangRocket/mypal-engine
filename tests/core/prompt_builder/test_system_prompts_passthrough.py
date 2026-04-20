"""Tests that PromptBuilder passes system_prompts through to build_worm_persona."""
from __future__ import annotations

from mypalclara.core.prompt_builder import PromptBuilder, PromptMode


def _extract_system_content(messages):
    """Return the concatenated text of all SystemMessage content."""
    from mypalclara.core.llm.messages import SystemMessage
    parts = []
    for m in messages:
        if isinstance(m, SystemMessage):
            parts.append(m.content)
    return "\n".join(parts)


def test_minimal_mode_passes_system_prompts():
    builder = PromptBuilder(agent_id="test")
    messages = builder.build_prompt(
        user_mems=[],
        proj_mems=[],
        thread_summary=None,
        recent_msgs=[],
        user_message="hi",
        tools=None,
        mode=PromptMode.MINIMAL,
        system_prompts=[("obsidian", "Prefer search before get_file.")],
    )
    system_text = _extract_system_content(messages)
    assert "Tool-specific guidance — obsidian" in system_text
    assert "Prefer search before get_file." in system_text


def test_minimal_mode_no_system_prompts():
    builder = PromptBuilder(agent_id="test")
    messages = builder.build_prompt(
        user_mems=[],
        proj_mems=[],
        thread_summary=None,
        recent_msgs=[],
        user_message="hi",
        tools=None,
        mode=PromptMode.MINIMAL,
    )
    system_text = _extract_system_content(messages)
    assert "Tool-specific guidance" not in system_text


def test_none_mode_ignores_system_prompts():
    """NONE mode uses PERSONALITY_BRIEF and shouldn't touch system_prompts."""
    builder = PromptBuilder(agent_id="test")
    messages = builder.build_prompt(
        user_mems=[],
        proj_mems=[],
        thread_summary=None,
        recent_msgs=[],
        user_message="hi",
        mode=PromptMode.NONE,
        system_prompts=[("obsidian", "guidance")],
    )
    system_text = _extract_system_content(messages)
    assert "Tool-specific guidance" not in system_text


def test_full_mode_passes_system_prompts():
    """FULL mode should also pass system_prompts to build_worm_persona."""
    builder = PromptBuilder(agent_id="test")
    messages = builder.build_prompt(
        user_mems=[],
        proj_mems=[],
        thread_summary=None,
        recent_msgs=[],
        user_message="hi",
        tools=None,
        mode=PromptMode.FULL,
        system_prompts=[("github", "Use gh CLI tool for reviews.")],
    )
    system_text = _extract_system_content(messages)
    assert "Tool-specific guidance — github" in system_text
    assert "Use gh CLI tool for reviews." in system_text
