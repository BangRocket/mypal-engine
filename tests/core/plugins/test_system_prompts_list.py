"""Tests for PluginRegistry.get_system_prompts_list (tuple-returning variant)."""
from __future__ import annotations


def test_get_system_prompts_list_returns_tuples():
    from mypalclara.core.plugins.registry import PluginRegistry

    reg = PluginRegistry()
    reg.register_system_prompt("obsidian", "Obsidian guidance.")
    reg.register_system_prompt("github", "GitHub guidance.")

    result = reg.get_system_prompts_list()
    assert result == [
        ("obsidian", "Obsidian guidance."),
        ("github", "GitHub guidance."),
    ]


def test_get_system_prompts_list_filters_by_allowed_modules():
    from mypalclara.core.plugins.registry import PluginRegistry

    reg = PluginRegistry()
    reg.register_system_prompt("a", "A")
    reg.register_system_prompt("b", "B")
    reg.register_system_prompt("c", "C")

    result = reg.get_system_prompts_list(allowed_modules=["a", "c"])
    assert result == [("a", "A"), ("c", "C")]


def test_get_system_prompts_list_empty_when_no_prompts():
    from mypalclara.core.plugins.registry import PluginRegistry

    reg = PluginRegistry()
    assert reg.get_system_prompts_list() == []


def test_get_system_prompts_list_empty_allowed_filter_returns_empty():
    from mypalclara.core.plugins.registry import PluginRegistry

    reg = PluginRegistry()
    reg.register_system_prompt("a", "A")
    reg.register_system_prompt("b", "B")

    result = reg.get_system_prompts_list(allowed_modules=[])
    assert result == []
