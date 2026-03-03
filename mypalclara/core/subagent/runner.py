"""Sub-agent execution runner."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus

logger = logging.getLogger(__name__)


class SubagentRunner:
    """Executes sub-agents as asyncio tasks with timeout and kill support."""

    def __init__(
        self,
        registry: SubagentRegistry,
        orchestrator_factory: Callable,
        timeout_seconds: float = 600,
    ) -> None:
        self._registry = registry
        self._orchestrator_factory = orchestrator_factory
        self._timeout_seconds = timeout_seconds

    async def run(
        self,
        agent_id: str,
        tools: list[Any],
        user_id: str,
    ) -> str:
        """Run a sub-agent to completion, timeout, or kill.

        Returns the result content string on success, or error description on failure.
        """
        record = self._registry.get(agent_id)
        if record is None:
            return f"Agent {agent_id} not found"

        try:
            result = await asyncio.wait_for(
                self._execute(agent_id, tools, user_id),
                timeout=self._timeout_seconds,
            )
            return result
        except asyncio.TimeoutError:
            self._registry.fail(agent_id, "Timed out")
            logger.warning("Sub-agent %s timed out after %ss", agent_id, self._timeout_seconds)
            return f"Agent {agent_id} timed out"
        except Exception as exc:
            self._registry.fail(agent_id, str(exc))
            logger.error("Sub-agent %s failed: %s", agent_id, exc)
            return f"Agent {agent_id} failed: {exc}"

    async def _execute(
        self,
        agent_id: str,
        tools: list[Any],
        user_id: str,
    ) -> str:
        """Core execution loop: consume orchestrator output, check for kill/steering."""
        record = self._registry.get(agent_id)
        if record is None:
            return f"Agent {agent_id} not found"

        orchestrator = self._orchestrator_factory()
        messages = [{"role": "user", "content": record.task}]

        result_content = ""
        gen = orchestrator.generate_with_tools(messages, tools)

        async for event in gen:
            # Check kill signal
            if record.status == SubagentStatus.KILLED:
                logger.info("Sub-agent %s was killed during execution", agent_id)
                return f"Agent {agent_id} killed"

            # Inject any steering instructions
            steering = self._registry.pop_steering(agent_id)
            for instruction in steering:
                messages.append({"role": "user", "content": f"[STEERING] {instruction}"})

            event_type = event.get("type", "")
            if event_type == "complete":
                result_content = event.get("content", "")
            elif event_type == "tool_call":
                # Tool calls would be handled by the orchestrator
                pass
            elif event_type == "error":
                error_msg = event.get("content", "Unknown error")
                self._registry.fail(agent_id, error_msg)
                return error_msg

        # If we consumed the full generator without error, mark complete
        if record.status == SubagentStatus.RUNNING:
            self._registry.complete(agent_id, result_content)

        return result_content
