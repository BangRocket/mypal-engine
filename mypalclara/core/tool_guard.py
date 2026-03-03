"""Tool loop detection for the LLM orchestrator.

Detects repetitive tool call patterns and signals when the LLM is stuck:
- Generic repeat: same tool+args called too many times
- Poll no progress: same tool+args returning identical results
- Ping-pong: alternating A-B-A-B call patterns
- Circuit breaker: global hard stop on identical no-progress calls
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any


class LoopAction(Enum):
    """Action the orchestrator should take."""

    ALLOW = "allow"
    WARN = "warn"
    STOP = "stop"


@dataclass(frozen=True)
class LoopCheckResult:
    """Result of a loop guard check."""

    action: LoopAction
    reason: str | None = None
    pattern: str | None = None


# Thresholds
WARN_REPEAT_THRESHOLD = 10
STOP_REPEAT_THRESHOLD = 30
POLL_NO_PROGRESS_THRESHOLD = 4  # consecutive identical results before stop
PING_PONG_CYCLES = 2  # full A-B cycles before stop
WINDOW_SIZE = 30


def _hash_call(tool_name: str, args: dict[str, Any]) -> str:
    """SHA-256 hash of tool_name + sorted JSON args."""
    raw = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_result(result: str) -> str:
    """SHA-256 hash of the result string."""
    return hashlib.sha256(result.encode()).hexdigest()


class ToolLoopGuard:
    """Detects repetitive tool call patterns."""

    def __init__(self) -> None:
        self._call_hashes: list[str] = []
        self._result_hashes: dict[str, list[str]] = defaultdict(list)

    def reset(self) -> None:
        """Clear all tracking state."""
        self._call_hashes.clear()
        self._result_hashes.clear()

    def check(self, tool_name: str, args: dict[str, Any]) -> LoopCheckResult:
        """Check if this tool call looks like a loop.

        Must be called BEFORE tool execution. Adds the call to the window.
        """
        call_hash = _hash_call(tool_name, args)
        self._call_hashes.append(call_hash)

        # Enforce sliding window
        if len(self._call_hashes) > WINDOW_SIZE:
            self._call_hashes = self._call_hashes[-WINDOW_SIZE:]

        # Check order: circuit_breaker -> poll_no_progress -> ping_pong -> generic_repeat
        result = self._check_circuit_breaker(call_hash)
        if result:
            return result

        result = self._check_poll_no_progress(call_hash)
        if result:
            return result

        result = self._check_ping_pong()
        if result:
            return result

        result = self._check_generic_repeat(call_hash)
        if result:
            return result

        return LoopCheckResult(action=LoopAction.ALLOW)

    def record_result(
        self, tool_name: str, args: dict[str, Any], result: str
    ) -> None:
        """Record a tool result for progress tracking.

        Must be called AFTER tool execution.
        """
        call_hash = _hash_call(tool_name, args)
        result_hash = _hash_result(result)
        self._result_hashes[call_hash].append(result_hash)

    def _check_circuit_breaker(self, call_hash: str) -> LoopCheckResult | None:
        """Global hard stop: 30+ identical calls with no progress."""
        count = self._call_hashes.count(call_hash)
        if count < STOP_REPEAT_THRESHOLD:
            return None

        # Check if results have been unchanging
        result_list = self._result_hashes.get(call_hash, [])
        if len(result_list) < STOP_REPEAT_THRESHOLD - 1:
            return None

        # Check if all recent results are identical
        recent = result_list[-(STOP_REPEAT_THRESHOLD - 1) :]
        if len(set(recent)) == 1:
            return LoopCheckResult(
                action=LoopAction.STOP,
                reason=f"Circuit breaker: {count} identical calls with no progress",
                pattern="circuit_breaker",
            )
        return None

    def _check_poll_no_progress(self, call_hash: str) -> LoopCheckResult | None:
        """Same tool+args returning identical results N times in a row."""
        result_list = self._result_hashes.get(call_hash, [])
        if len(result_list) < POLL_NO_PROGRESS_THRESHOLD:
            return None

        # Check the last N results are identical
        recent = result_list[-POLL_NO_PROGRESS_THRESHOLD:]
        if len(set(recent)) == 1:
            return LoopCheckResult(
                action=LoopAction.STOP,
                reason=(
                    f"Poll no progress: {POLL_NO_PROGRESS_THRESHOLD} consecutive "
                    f"identical results"
                ),
                pattern="poll_no_progress",
            )
        return None

    def _check_ping_pong(self) -> LoopCheckResult | None:
        """Detect alternating A-B-A-B patterns."""
        hashes = self._call_hashes
        if len(hashes) < PING_PONG_CYCLES * 2 + 1:
            return None

        # Check if the last calls form an alternating pattern
        # Need at least 2 full cycles + 1 (A-B-A-B-A for 2 cycles)
        required = PING_PONG_CYCLES * 2 + 1
        tail = hashes[-required:]

        a = tail[0]
        b = tail[1]
        if a == b:
            return None

        is_ping_pong = True
        for i, h in enumerate(tail):
            expected = a if i % 2 == 0 else b
            if h != expected:
                is_ping_pong = False
                break

        if is_ping_pong:
            return LoopCheckResult(
                action=LoopAction.STOP,
                reason=f"Ping-pong: {PING_PONG_CYCLES}+ alternating cycles detected",
                pattern="ping_pong",
            )
        return None

    def _check_generic_repeat(self, call_hash: str) -> LoopCheckResult | None:
        """Same tool+args called too many times."""
        count = self._call_hashes.count(call_hash)
        if count >= STOP_REPEAT_THRESHOLD:
            return LoopCheckResult(
                action=LoopAction.STOP,
                reason=f"Generic repeat: {count} identical calls (hard stop)",
                pattern="generic_repeat",
            )
        if count >= WARN_REPEAT_THRESHOLD:
            return LoopCheckResult(
                action=LoopAction.WARN,
                reason=f"Generic repeat: {count} identical calls",
                pattern="generic_repeat",
            )
        return None
