"""Tests for per-user availability filtering in ToolExecutor.get_all_tools."""

from __future__ import annotations

import pytest

from mypalclara.gateway.tool_executor import ToolExecutor
from mypalclara.tools._base import ToolContext, ToolDef
from mypalclara.tools._registry import ToolRegistry

pytestmark = pytest.mark.asyncio


async def _handler(args: dict, ctx: ToolContext) -> str:
    return "ok"


def _fresh_registry() -> ToolRegistry:
    """Build a clean ToolRegistry instance backed by a fresh PluginRegistry.

    Using an instance (not the global singleton) keeps tests isolated from any
    real tools that happen to be registered in the shared registry.
    """
    from mypalclara.core.plugins.registry import PluginRegistry

    registry = ToolRegistry()
    registry._plugin_registry = PluginRegistry()
    return registry


@pytest.fixture
def registry_with_tools():
    """Build a registry holding a free tool and three tools that share one predicate."""

    async def alice_only(uid: str) -> bool:
        return uid == "alice"

    t_free = ToolDef(
        name="free_tool",
        description="available to everyone",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    )
    t_alice = ToolDef(
        name="alice_tool",
        description="alice only",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        availability=alice_only,
    )
    # Two more tools that share the SAME predicate (tests memoization)
    t_alice2 = ToolDef(
        name="alice_tool_2",
        description="alice only #2",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        availability=alice_only,
    )
    t_alice3 = ToolDef(
        name="alice_tool_3",
        description="alice only #3",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        availability=alice_only,
    )

    registry = _fresh_registry()
    registry.register(t_free, source_module="test_mod")
    registry.register(t_alice, source_module="test_mod")
    registry.register(t_alice2, source_module="test_mod")
    registry.register(t_alice3, source_module="test_mod")
    return registry, alice_only


def _make_executor_with_registry(registry: ToolRegistry) -> ToolExecutor:
    """Create a ToolExecutor pre-wired with a registry.

    MCP/Discord/subagent subsystems stay un-initialized — the test only exercises
    the modular path.
    """
    executor = ToolExecutor()
    executor._tool_registry = registry
    executor._modular_initialized = True
    executor._initialized = True
    return executor


async def test_unfiltered_when_user_id_is_none(registry_with_tools):
    registry, _ = registry_with_tools
    executor = _make_executor_with_registry(registry)
    tools = await executor.get_all_tools()  # no user_id
    names = {t["function"]["name"] for t in tools if t.get("type") == "function"}
    assert {"free_tool", "alice_tool", "alice_tool_2", "alice_tool_3"} <= names


async def test_alice_sees_all_tools(registry_with_tools):
    registry, _ = registry_with_tools
    executor = _make_executor_with_registry(registry)
    tools = await executor.get_all_tools(user_id="alice")
    names = {t["function"]["name"] for t in tools if t.get("type") == "function"}
    assert "free_tool" in names
    assert "alice_tool" in names
    assert "alice_tool_2" in names
    assert "alice_tool_3" in names


async def test_bob_sees_only_free_tool(registry_with_tools):
    registry, _ = registry_with_tools
    executor = _make_executor_with_registry(registry)
    tools = await executor.get_all_tools(user_id="bob")
    names = {t["function"]["name"] for t in tools if t.get("type") == "function"}
    assert "free_tool" in names
    assert "alice_tool" not in names
    assert "alice_tool_2" not in names
    assert "alice_tool_3" not in names


async def test_predicate_memoized_per_invocation():
    """Three tools sharing a predicate should only invoke it once per get_all_tools call."""
    call_count = {"n": 0}

    async def shared(uid: str) -> bool:
        call_count["n"] += 1
        return True

    tools = [
        ToolDef(
            name=f"shared_t{i}",
            description="d",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            availability=shared,
        )
        for i in range(3)
    ]
    registry = _fresh_registry()
    for t in tools:
        registry.register(t, source_module="m")
    executor = _make_executor_with_registry(registry)

    await executor.get_all_tools(user_id="alice")
    assert call_count["n"] == 1  # memoized across all three tools

    # Second invocation increments the counter again (fresh memo per call)
    await executor.get_all_tools(user_id="alice")
    assert call_count["n"] == 2


async def test_predicate_raising_hides_tool():
    async def boom(uid: str) -> bool:
        raise RuntimeError("db down")

    bad = ToolDef(
        name="bad_tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
        availability=boom,
    )
    good = ToolDef(
        name="good_tool",
        description="d",
        parameters={"type": "object", "properties": {}},
        handler=_handler,
    )
    registry = _fresh_registry()
    registry.register(bad, source_module="m")
    registry.register(good, source_module="m")
    executor = _make_executor_with_registry(registry)

    tools = await executor.get_all_tools(user_id="alice")
    names = {t["function"]["name"] for t in tools if t.get("type") == "function"}
    assert "good_tool" in names
    assert "bad_tool" not in names
