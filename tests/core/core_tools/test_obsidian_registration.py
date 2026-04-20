"""Smoke test that register_core_tools registers obsidian_tool."""

from __future__ import annotations

import asyncio


def test_obsidian_tool_is_registered_by_register_core_tools():
    """Call register_core_tools against a fresh ToolRegistry and verify all 16
    obsidian_* tools landed."""
    from mypalclara.core.core_tools import register_core_tools
    from mypalclara.core.plugins.registry import PluginRegistry
    from mypalclara.tools._registry import ToolRegistry

    # Fresh, isolated registry — not the global singleton.
    registry = ToolRegistry()
    registry._plugin_registry = PluginRegistry()

    asyncio.run(register_core_tools(registry))

    pr = registry._get_plugin_registry()
    obs_names = [name for name in pr.tools if name.startswith("obsidian_")]
    assert len(obs_names) == 16, (
        f"expected 16 obsidian tools, got {len(obs_names)}: {sorted(obs_names)}"
    )

    # System prompt registered under the module name.
    assert "obsidian" in pr.system_prompts
    assert len(pr.system_prompts["obsidian"]) > 100
