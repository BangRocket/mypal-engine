"""Circuit breaker for tool execution resilience.

Tracks consecutive failures per tool and temporarily disables tools
that are consistently failing, preventing cascading failures and
wasted LLM iterations on broken tools.

States:
    CLOSED  - Normal operation, all calls allowed
    OPEN    - Tool is failing, calls blocked until cooldown expires
    HALF_OPEN - Cooldown expired, allowing limited test calls
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3
    cooldown_seconds: float = 300.0
    half_open_max_calls: int = 1


@dataclass
class CircuitStats:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    last_failure_time: float = 0.0
    last_failure_error: str = ""
    opened_at: float = 0.0
    success_count: int = 0
    total_calls: int = 0
    half_open_calls: int = 0


class CircuitBreaker:
    """Per-tool circuit breaker.

    Tracks failure counts for each tool independently. When a tool
    hits the failure threshold, it's blocked for a cooldown period
    before a single test call is allowed.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self._circuits: dict[str, CircuitStats] = {}
        self.config = config or CircuitBreakerConfig()

    def _get_stats(self, tool_name: str) -> CircuitStats:
        if tool_name not in self._circuits:
            self._circuits[tool_name] = CircuitStats()
        return self._circuits[tool_name]

    def can_execute(self, tool_name: str) -> tuple[bool, str | None]:
        """Check if a tool can be called.

        Args:
            tool_name: Name of the tool

        Returns:
            (allowed, reason_if_blocked)
        """
        stats = self._get_stats(tool_name)

        if stats.state == CircuitState.CLOSED:
            return True, None

        if stats.state == CircuitState.OPEN:
            elapsed = time.monotonic() - stats.opened_at
            if elapsed >= self.config.cooldown_seconds:
                # Transition to half-open
                stats.state = CircuitState.HALF_OPEN
                stats.half_open_calls = 0
                return True, None
            remaining = self.config.cooldown_seconds - elapsed
            return False, (
                f"{tool_name} circuit open after {stats.failure_count} consecutive failures "
                f"(last error: {stats.last_failure_error}). "
                f"Retry in {remaining:.0f}s."
            )

        if stats.state == CircuitState.HALF_OPEN:
            if stats.half_open_calls < self.config.half_open_max_calls:
                stats.half_open_calls += 1
                return True, None
            return False, (f"{tool_name} circuit half-open, test call limit reached. " f"Waiting for test result.")

        return True, None

    def record_success(self, tool_name: str) -> None:
        """Record a successful execution.

        Resets failure count. HALF_OPEN transitions to CLOSED.
        """
        stats = self._get_stats(tool_name)
        stats.total_calls += 1
        stats.success_count += 1
        stats.failure_count = 0

        if stats.state == CircuitState.HALF_OPEN:
            stats.state = CircuitState.CLOSED
            stats.half_open_calls = 0

    def record_failure(self, tool_name: str, error: str) -> None:
        """Record a failed execution.

        Increments failure count. CLOSED transitions to OPEN when
        threshold is reached. HALF_OPEN transitions back to OPEN.
        """
        stats = self._get_stats(tool_name)
        stats.total_calls += 1
        stats.failure_count += 1
        stats.last_failure_time = time.monotonic()
        stats.last_failure_error = error[:200]

        if stats.state == CircuitState.HALF_OPEN:
            # Test call failed — reopen
            stats.state = CircuitState.OPEN
            stats.opened_at = time.monotonic()
            stats.half_open_calls = 0
        elif stats.state == CircuitState.CLOSED:
            if stats.failure_count >= self.config.failure_threshold:
                stats.state = CircuitState.OPEN
                stats.opened_at = time.monotonic()

    def get_health_summary(self) -> dict[str, Any]:
        """Return health status for all tracked tools.

        Returns:
            Dict mapping tool names to their circuit status
        """
        summary: dict[str, Any] = {}
        for name, stats in self._circuits.items():
            summary[name] = {
                "state": stats.state.value,
                "failure_count": stats.failure_count,
                "success_count": stats.success_count,
                "total_calls": stats.total_calls,
                "last_failure_error": stats.last_failure_error or None,
            }
        return summary

    def reset(self, tool_name: str) -> None:
        """Manually reset a circuit to CLOSED."""
        if tool_name in self._circuits:
            self._circuits[tool_name] = CircuitStats()
