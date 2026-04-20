"""Tests for the optional per-user availability predicate on ToolDef."""
from __future__ import annotations

import pytest

from mypalclara.tools._base import ToolContext, ToolDef


async def _noop_handler(args: dict, ctx: ToolContext) -> str:
    return "ok"


async def _always_true(user_id: str) -> bool:
    return True


async def _always_false(user_id: str) -> bool:
    return False


def test_availability_defaults_to_none():
    tool = ToolDef(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
    )
    assert tool.availability is None


def test_availability_accepts_async_callable():
    tool = ToolDef(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        availability=_always_true,
    )
    assert tool.availability is _always_true


def test_availability_can_be_async_lambda_style():
    async def pred(uid: str) -> bool:
        return uid == "alice"

    tool = ToolDef(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        availability=pred,
    )
    assert tool.availability is pred


@pytest.mark.asyncio
async def test_availability_predicate_invocation():
    """Calling the predicate from a ToolDef works as expected."""
    tool = ToolDef(
        name="t",
        description="d",
        parameters={},
        handler=_noop_handler,
        availability=_always_false,
    )
    assert tool.availability is not None
    result = await tool.availability("any-user")
    assert result is False


def test_existing_fields_unchanged():
    """Adding `availability` must not affect existing ToolDef behavior."""
    tool = ToolDef(
        name="my_tool",
        description="does things",
        parameters={"type": "object"},
        handler=_noop_handler,
        platforms=["discord"],
        requires=["docker"],
        emoji="🚀",
        label="Launch",
        detail_keys=["query"],
        risk_level="moderate",
        intent="execute",
    )
    assert tool.name == "my_tool"
    assert tool.risk_level == "moderate"
    assert tool.intent == "execute"
    assert tool.availability is None
