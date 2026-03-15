"""Sub-agent registry for lifecycle management."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class SubagentStatus(Enum):
    """Lifecycle states for a sub-agent."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


@dataclass
class SubagentRunRecord:
    """Tracks a single sub-agent execution."""

    id: str
    parent_id: str
    session_key: str
    task: str
    status: SubagentStatus = SubagentStatus.RUNNING
    model_tier: str = "mid"
    token_usage: int = 0
    start_time: float = field(default_factory=time.monotonic)
    result_summary: str | None = None
    tool_subset: list[str] | None = None
    _steering_queue: list[str] = field(default_factory=list)
    _last_steer_time: float = 0.0


class SubagentRegistry:
    """Manages sub-agent lifecycle: registration, status, steering."""

    def __init__(self, max_per_parent: int = 5) -> None:
        self._agents: dict[str, SubagentRunRecord] = {}
        self._max_per_parent = max_per_parent

    def register(
        self,
        parent_id: str,
        task: str,
        tier: str = "mid",
        tool_subset: list[str] | None = None,
    ) -> SubagentRunRecord:
        """Register a new sub-agent. Raises RuntimeError if parent exceeds limit."""
        active_count = len(self.list_active(parent_id))
        if active_count >= self._max_per_parent:
            raise RuntimeError(
                f"Parent {parent_id} has reached the maximum of " f"{self._max_per_parent} concurrent sub-agents"
            )

        agent_id = uuid.uuid4().hex[:8]
        session_key = f"agent:{parent_id}:sub:{agent_id}"

        record = SubagentRunRecord(
            id=agent_id,
            parent_id=parent_id,
            session_key=session_key,
            task=task,
            model_tier=tier,
            tool_subset=tool_subset,
        )
        self._agents[agent_id] = record
        return record

    def get(self, agent_id: str) -> SubagentRunRecord | None:
        """Return a sub-agent record by ID, or None."""
        return self._agents.get(agent_id)

    def list_active(self, parent_id: str | None = None) -> list[SubagentRunRecord]:
        """List all RUNNING sub-agents, optionally filtered by parent."""
        return [
            r
            for r in self._agents.values()
            if r.status == SubagentStatus.RUNNING and (parent_id is None or r.parent_id == parent_id)
        ]

    def list_all(self, parent_id: str | None = None) -> list[SubagentRunRecord]:
        """List all sub-agents regardless of status, optionally filtered by parent."""
        return [r for r in self._agents.values() if parent_id is None or r.parent_id == parent_id]

    def kill(self, agent_id: str) -> None:
        """Mark a sub-agent as KILLED."""
        record = self._agents.get(agent_id)
        if record is not None:
            record.status = SubagentStatus.KILLED

    def kill_all(self, parent_id: str) -> int:
        """Kill all RUNNING sub-agents for a parent. Returns count killed."""
        killed = 0
        for record in self.list_active(parent_id):
            record.status = SubagentStatus.KILLED
            killed += 1
        return killed

    def complete(self, agent_id: str, result_summary: str) -> None:
        """Mark a sub-agent as COMPLETED with a result summary."""
        record = self._agents.get(agent_id)
        if record is not None:
            record.status = SubagentStatus.COMPLETED
            record.result_summary = result_summary

    def fail(self, agent_id: str, error: str) -> None:
        """Mark a sub-agent as FAILED with an error description."""
        record = self._agents.get(agent_id)
        if record is not None:
            record.status = SubagentStatus.FAILED
            record.result_summary = error

    def steer(self, agent_id: str, instruction: str) -> None:
        """Queue a steering instruction. Rate limited to 2s intervals.

        Raises RuntimeError if agent is not running or rate limit exceeded.
        """
        record = self._agents.get(agent_id)
        if record is None or record.status != SubagentStatus.RUNNING:
            raise RuntimeError(f"Agent {agent_id} is not running")

        now = time.monotonic()
        if record._last_steer_time > 0 and (now - record._last_steer_time) < 2.0:
            raise RuntimeError("Steering rate limit: must wait at least 2s between instructions")

        record._steering_queue.append(instruction)
        record._last_steer_time = now

    def pop_steering(self, agent_id: str) -> list[str]:
        """Pop all queued steering instructions. Returns [] if agent not found."""
        record = self._agents.get(agent_id)
        if record is None:
            return []
        instructions = list(record._steering_queue)
        record._steering_queue.clear()
        return instructions
