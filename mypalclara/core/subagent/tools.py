"""Sub-agent tools for LLM-driven orchestration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus
from mypalclara.core.subagent.runner import SubagentRunner

logger = logging.getLogger(__name__)


def make_subagent_tools(
    registry: SubagentRegistry,
    runner: SubagentRunner,
) -> list[dict[str, Any]]:
    """Return tool definitions for sub-agent orchestration."""
    return [
        {
            "name": "subagent_spawn",
            "description": (
                "Spawn a sub-agent to work on a task in parallel. "
                "The sub-agent runs independently and reports results when done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The task for the sub-agent to perform.",
                    },
                    "tier": {
                        "type": "string",
                        "description": "Model tier: 'low', 'mid', or 'high'.",
                        "default": "mid",
                    },
                    "tool_subset": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of tool names the sub-agent may use.",
                    },
                },
                "required": ["task"],
            },
        },
        {
            "name": "subagent_list",
            "description": "List active and recent sub-agents with their status.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "subagent_kill",
            "description": "Kill a running sub-agent by ID, or 'all' to kill all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Sub-agent ID to kill, or 'all'.",
                    },
                },
                "required": ["id"],
            },
        },
        {
            "name": "subagent_steer",
            "description": (
                "Send a corrective instruction to a running sub-agent. "
                "Rate limited to one instruction every 2 seconds."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Sub-agent ID to steer.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Corrective instruction for the sub-agent.",
                    },
                },
                "required": ["id", "instruction"],
            },
        },
    ]


async def handle_subagent_tool(
    tool_name: str,
    arguments: dict[str, Any],
    parent_id: str,
    registry: SubagentRegistry,
    runner: SubagentRunner,
    available_tools: list[Any],
    user_id: str,
) -> str:
    """Dispatch a sub-agent tool call and return a result string."""
    if tool_name == "subagent_spawn":
        return await _handle_spawn(arguments, parent_id, registry, runner, available_tools, user_id)
    elif tool_name == "subagent_list":
        return _handle_list(parent_id, registry)
    elif tool_name == "subagent_kill":
        return _handle_kill(arguments, parent_id, registry)
    elif tool_name == "subagent_steer":
        return _handle_steer(arguments, registry)
    else:
        return f"Unknown subagent tool: {tool_name}"


async def _handle_spawn(
    arguments: dict[str, Any],
    parent_id: str,
    registry: SubagentRegistry,
    runner: SubagentRunner,
    available_tools: list[Any],
    user_id: str,
) -> str:
    """Spawn a new sub-agent."""
    task = arguments.get("task", "")
    tier = arguments.get("tier", "mid")
    tool_subset = arguments.get("tool_subset")

    try:
        record = registry.register(parent_id, task, tier=tier, tool_subset=tool_subset)
    except RuntimeError as exc:
        return f"Failed to spawn sub-agent: {exc}"

    # Filter tools if a subset was specified
    tools = available_tools
    if tool_subset:
        tools = [t for t in available_tools if getattr(t, "name", None) in tool_subset]

    # Fire and forget the runner as a background task
    asyncio.create_task(runner.run(record.id, tools, user_id))

    return f"Spawned sub-agent {record.id} (tier={tier}): {task}\n" f"Session: {record.session_key}"


def _handle_list(parent_id: str, registry: SubagentRegistry) -> str:
    """List sub-agents for this parent."""
    agents = registry.list_all(parent_id)
    if not agents:
        return "No sub-agents found."

    lines = []
    for agent in agents:
        elapsed = ""
        summary = ""
        if agent.result_summary:
            summary = f" — {agent.result_summary[:100]}"
        lines.append(f"  [{agent.id}] {agent.status.value}: {agent.task[:80]}{summary}")

    return f"Sub-agents ({len(agents)}):\n" + "\n".join(lines)


def _handle_kill(
    arguments: dict[str, Any],
    parent_id: str,
    registry: SubagentRegistry,
) -> str:
    """Kill a sub-agent or all sub-agents."""
    target_id = arguments.get("id", "")

    if target_id == "all":
        killed = registry.kill_all(parent_id)
        return f"Killed {killed} sub-agent(s)."

    record = registry.get(target_id)
    if record is None:
        return f"Sub-agent {target_id} not found."

    registry.kill(target_id)
    return f"Killed sub-agent {target_id}."


def _handle_steer(
    arguments: dict[str, Any],
    registry: SubagentRegistry,
) -> str:
    """Send a steering instruction to a sub-agent."""
    agent_id = arguments.get("id", "")
    instruction = arguments.get("instruction", "")

    try:
        registry.steer(agent_id, instruction)
    except RuntimeError as exc:
        return f"Steering failed: {exc}"

    return f"Steering instruction sent to sub-agent {agent_id}."
