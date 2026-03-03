# OpenClaw-Inspired Improvements Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 8 features inspired by OpenClaw's architecture: tool loop detection, tool result capping, provider failover, compositional prompt builder, workspace files, tool summaries, context compaction, and subagent orchestration.

**Architecture:** Each feature is a standalone module that integrates at well-defined points in the existing gateway/LLM pipeline. Features are ordered by dependency: independent reliability features first, then the prompt builder refactor (foundation), then features that depend on it.

**Tech Stack:** Python 3.11+, asyncio, pytest, pytest-asyncio, tiktoken, hashlib (SHA-256)

**Design doc:** `docs/plans/2026-03-03-openclaw-inspired-improvements-design.md`

---

## Task 1: Tool Loop Detection

**Files:**
- Create: `mypalclara/core/tool_guard.py`
- Create: `tests/clara_core/test_tool_guard.py`
- Modify: `mypalclara/gateway/llm_orchestrator.py:76-319`

### Step 1: Write failing tests for ToolLoopGuard

Create `tests/clara_core/test_tool_guard.py`:

```python
"""Tests for tool loop detection."""
import pytest
from mypalclara.core.tool_guard import ToolLoopGuard, LoopAction


class TestGenericRepeat:
    """Generic repeat detection: warn at 10, stop at 30."""

    def test_allow_normal_calls(self):
        guard = ToolLoopGuard()
        for i in range(9):
            result = guard.check(f"tool_{i}", {"arg": i})
            assert result.action == LoopAction.ALLOW

    def test_warn_at_10_identical_calls(self):
        guard = ToolLoopGuard()
        for i in range(9):
            guard.check("read_file", {"path": "/foo"})
            guard.record_result("read_file", {"path": "/foo"}, f"result_{i}")
        result = guard.check("read_file", {"path": "/foo"})
        assert result.action == LoopAction.WARN
        assert result.pattern == "generic_repeat"

    def test_stop_at_30_identical_calls(self):
        guard = ToolLoopGuard()
        for i in range(29):
            guard.check("read_file", {"path": "/foo"})
            guard.record_result("read_file", {"path": "/foo"}, f"result_{i}")
        result = guard.check("read_file", {"path": "/foo"})
        assert result.action == LoopAction.STOP
        assert result.pattern == "generic_repeat"

    def test_different_args_not_repeated(self):
        guard = ToolLoopGuard()
        for i in range(15):
            result = guard.check("read_file", {"path": f"/file_{i}"})
            assert result.action == LoopAction.ALLOW
            guard.record_result("read_file", {"path": f"/file_{i}"}, f"content_{i}")


class TestPollNoProgress:
    """Poll no progress: stop when result hash unchanged for 5+ calls."""

    def test_stop_on_unchanged_results(self):
        guard = ToolLoopGuard()
        for i in range(4):
            guard.check("get_status", {"id": "123"})
            guard.record_result("get_status", {"id": "123"}, "status: pending")
        result = guard.check("get_status", {"id": "123"})
        assert result.action == LoopAction.STOP
        assert result.pattern == "poll_no_progress"

    def test_allow_when_results_change(self):
        guard = ToolLoopGuard()
        for i in range(10):
            guard.check("get_status", {"id": "123"})
            guard.record_result("get_status", {"id": "123"}, f"status: step_{i}")
        result = guard.check("get_status", {"id": "123"})
        assert result.action != LoopAction.STOP


class TestPingPong:
    """Ping-pong: detect alternating A-B-A-B patterns."""

    def test_detect_ping_pong(self):
        guard = ToolLoopGuard()
        for _ in range(2):
            guard.check("read_file", {"path": "/a"})
            guard.record_result("read_file", {"path": "/a"}, "content_a")
            guard.check("write_file", {"path": "/b"})
            guard.record_result("write_file", {"path": "/b"}, "ok")
        result = guard.check("read_file", {"path": "/a"})
        assert result.action == LoopAction.STOP
        assert result.pattern == "ping_pong"

    def test_no_false_positive_with_variation(self):
        guard = ToolLoopGuard()
        guard.check("read_file", {"path": "/a"})
        guard.record_result("read_file", {"path": "/a"}, "content_a")
        guard.check("write_file", {"path": "/b"})
        guard.record_result("write_file", {"path": "/b"}, "ok")
        guard.check("read_file", {"path": "/a"})
        guard.record_result("read_file", {"path": "/a"}, "content_a")
        guard.check("search", {"q": "something"})
        guard.record_result("search", {"q": "something"}, "results")
        result = guard.check("read_file", {"path": "/a"})
        assert result.action == LoopAction.ALLOW


class TestCircuitBreaker:
    """Global circuit breaker at 30 identical no-progress calls."""

    def test_circuit_breaker_hard_stop(self):
        guard = ToolLoopGuard()
        for i in range(29):
            guard.check("tool_a", {"x": 1})
            guard.record_result("tool_a", {"x": 1}, "same_result")
        result = guard.check("tool_a", {"x": 1})
        assert result.action == LoopAction.STOP


class TestReset:
    """Reset clears all state."""

    def test_reset_clears_history(self):
        guard = ToolLoopGuard()
        for i in range(15):
            guard.check("tool_a", {"x": 1})
            guard.record_result("tool_a", {"x": 1}, "same")
        guard.reset()
        result = guard.check("tool_a", {"x": 1})
        assert result.action == LoopAction.ALLOW
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_tool_guard.py -v`
Expected: ImportError — `mypalclara.core.tool_guard` does not exist

### Step 3: Implement ToolLoopGuard

Create `mypalclara/core/tool_guard.py`:

```python
"""Tool loop detection to prevent runaway agent behavior.

Four detection mechanisms:
1. Generic Repeat: warn at 10+ identical calls, stop at 30+
2. Poll No Progress: stop when result hash unchanged for 5+ calls
3. Ping-Pong: detect alternating A-B-A-B patterns (2+ full cycles)
4. Circuit Breaker: global hard stop at 30+ identical no-progress calls
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

WINDOW_SIZE = 30
GENERIC_WARN_THRESHOLD = 10
GENERIC_STOP_THRESHOLD = 30
POLL_NO_PROGRESS_THRESHOLD = 5
PING_PONG_CYCLE_THRESHOLD = 2
CIRCUIT_BREAKER_THRESHOLD = 30


class LoopAction(Enum):
    ALLOW = "allow"
    WARN = "warn"
    STOP = "stop"


@dataclass
class LoopCheckResult:
    action: LoopAction
    reason: str | None = None
    pattern: str | None = None


def _hash_call(tool_name: str, args: dict) -> str:
    raw = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _hash_result(result: str) -> str:
    return hashlib.sha256(result.encode()).hexdigest()[:16]


@dataclass
class ToolLoopGuard:
    """Sliding window tool loop detector."""

    _call_hashes: list[str] = field(default_factory=list)
    _result_hashes: dict[str, list[str]] = field(default_factory=dict)

    def check(self, tool_name: str, args: dict) -> LoopCheckResult:
        call_hash = _hash_call(tool_name, args)

        # Circuit breaker: count identical no-progress calls
        if self._check_circuit_breaker(call_hash):
            reason = f"Circuit breaker: {CIRCUIT_BREAKER_THRESHOLD}+ identical calls to {tool_name} with no progress"
            logger.warning(reason)
            return LoopCheckResult(LoopAction.STOP, reason, "circuit_breaker")

        # Poll no progress: same call, same result
        if self._check_poll_no_progress(call_hash):
            reason = f"Poll no progress: {POLL_NO_PROGRESS_THRESHOLD}+ calls to {tool_name} with identical results"
            logger.warning(reason)
            return LoopCheckResult(LoopAction.STOP, reason, "poll_no_progress")

        # Ping-pong: alternating A-B-A-B
        if self._check_ping_pong(call_hash):
            reason = f"Ping-pong pattern detected: alternating tool calls with no progress"
            logger.warning(reason)
            return LoopCheckResult(LoopAction.STOP, reason, "ping_pong")

        # Generic repeat
        count = self._call_hashes.count(call_hash)
        if count >= GENERIC_STOP_THRESHOLD - 1:
            reason = f"Generic repeat: {count + 1} identical calls to {tool_name}"
            logger.warning(reason)
            return LoopCheckResult(LoopAction.STOP, reason, "generic_repeat")
        if count >= GENERIC_WARN_THRESHOLD - 1:
            reason = f"Repeat warning: {count + 1} identical calls to {tool_name}"
            logger.info(reason)
            return LoopCheckResult(LoopAction.WARN, reason, "generic_repeat")

        self._call_hashes.append(call_hash)
        if len(self._call_hashes) > WINDOW_SIZE:
            self._call_hashes.pop(0)

        return LoopCheckResult(LoopAction.ALLOW)

    def record_result(self, tool_name: str, args: dict, result: str) -> None:
        call_hash = _hash_call(tool_name, args)
        result_hash = _hash_result(result)
        if call_hash not in self._result_hashes:
            self._result_hashes[call_hash] = []
        self._result_hashes[call_hash].append(result_hash)
        # Keep only recent results per call
        if len(self._result_hashes[call_hash]) > WINDOW_SIZE:
            self._result_hashes[call_hash].pop(0)

    def reset(self) -> None:
        self._call_hashes.clear()
        self._result_hashes.clear()

    def _check_poll_no_progress(self, call_hash: str) -> bool:
        results = self._result_hashes.get(call_hash, [])
        if len(results) < POLL_NO_PROGRESS_THRESHOLD:
            return False
        recent = results[-POLL_NO_PROGRESS_THRESHOLD:]
        return len(set(recent)) == 1

    def _check_ping_pong(self, call_hash: str) -> bool:
        if len(self._call_hashes) < PING_PONG_CYCLE_THRESHOLD * 2:
            return False
        recent = self._call_hashes[-(PING_PONG_CYCLE_THRESHOLD * 2):]
        if len(set(recent)) != 2:
            return False
        # Check strict alternation
        for i in range(len(recent) - 1):
            if recent[i] == recent[i + 1]:
                return False
        # Current call would continue the pattern
        return call_hash == recent[-2]

    def _check_circuit_breaker(self, call_hash: str) -> bool:
        count = self._call_hashes.count(call_hash)
        if count < CIRCUIT_BREAKER_THRESHOLD - 1:
            return False
        # All results for this call are the same
        results = self._result_hashes.get(call_hash, [])
        if not results:
            return False
        return len(set(results)) == 1
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_tool_guard.py -v`
Expected: All 12 tests PASS

### Step 5: Integrate into LLMOrchestrator

Modify `mypalclara/gateway/llm_orchestrator.py`:

At line 16 (imports), add:
```python
from mypalclara.core.tool_guard import ToolLoopGuard, LoopAction
```

In `__init__` (line 87), add:
```python
self._loop_guard = ToolLoopGuard()
```

In `generate_with_tools()`, before tool execution (around line 270), add loop guard check:
```python
# Before: output = await self._tool_executor.execute(...)
loop_check = self._loop_guard.check(tc.name, tc.arguments)
if loop_check.action == LoopAction.STOP:
    output = f"[LOOP DETECTED] {loop_check.reason}. Stopping tool calls — please respond to the user with what you have so far."
    working_messages.append(tc.to_result_message(output))
    # Force end of tool loop
    break
elif loop_check.action == LoopAction.WARN:
    logger.warning(f"Tool loop warning: {loop_check.reason}")

output = await self._tool_executor.execute(...)
# After truncation/sandboxing, record result:
self._loop_guard.record_result(tc.name, tc.arguments, output)
```

### Step 6: Run tests to verify nothing broke

Run: `poetry run pytest tests/ -v --timeout=30`
Expected: All existing tests PASS

### Step 7: Lint and commit

```bash
poetry run ruff check mypalclara/core/tool_guard.py tests/clara_core/test_tool_guard.py && poetry run ruff format mypalclara/core/tool_guard.py tests/clara_core/test_tool_guard.py
git add mypalclara/core/tool_guard.py tests/clara_core/test_tool_guard.py mypalclara/gateway/llm_orchestrator.py
git commit -m "feat: add tool loop detection (OpenClaw-inspired)

Four detection mechanisms: generic repeat (warn@10, stop@30),
poll-no-progress (5+ identical results), ping-pong (A-B-A-B),
and global circuit breaker (30+ no-progress). Integrated into
LLMOrchestrator tool loop."
```

---

## Task 2: Tool Result Size Capping

**Files:**
- Create: `mypalclara/core/tool_result_guard.py`
- Create: `tests/clara_core/test_tool_result_guard.py`
- Modify: `mypalclara/gateway/llm_orchestrator.py:278,517-525`

### Step 1: Write failing tests

Create `tests/clara_core/test_tool_result_guard.py`:

```python
"""Tests for intelligent tool result size capping."""
import json
import pytest
from mypalclara.core.tool_result_guard import ToolResultGuard


class TestTextTruncation:
    """70/20 head/tail split for text content."""

    def test_short_text_not_truncated(self):
        guard = ToolResultGuard(max_chars=1000)
        result = guard.cap("tool", "call_1", "short text")
        assert result.content == "short text"
        assert not result.was_truncated

    def test_long_text_70_20_split(self):
        guard = ToolResultGuard(max_chars=100)
        text = "A" * 200
        result = guard.cap("tool", "call_1", text)
        assert result.was_truncated
        assert result.strategy == "text_70_20"
        assert len(result.content) <= 120  # 100 + marker overhead
        assert result.content.startswith("A" * 70)
        assert result.content.endswith("A" * 20)
        assert "truncated" in result.content

    def test_preserves_original_size(self):
        guard = ToolResultGuard(max_chars=100)
        text = "X" * 500
        result = guard.cap("tool", "call_1", text)
        assert result.original_size == 500


class TestJsonTruncation:
    """JSON-aware truncation: preserve structure, trim arrays."""

    def test_small_json_not_truncated(self):
        guard = ToolResultGuard(max_chars=1000)
        data = json.dumps({"key": "value"})
        result = guard.cap("tool", "call_1", data)
        assert not result.was_truncated

    def test_large_json_array_trimmed(self):
        guard = ToolResultGuard(max_chars=200)
        data = json.dumps({"items": [f"item_{i}" for i in range(100)]})
        result = guard.cap("tool", "call_1", data)
        assert result.was_truncated
        assert result.strategy == "json"
        parsed = json.loads(result.content.split("\n...[")[0])
        # Should keep first 3 and last 2
        assert len(parsed["items"]) == 5

    def test_invalid_json_falls_back_to_text(self):
        guard = ToolResultGuard(max_chars=100)
        text = "{not valid json" + "x" * 200
        result = guard.cap("tool", "call_1", text)
        assert result.was_truncated
        assert result.strategy == "text_70_20"


class TestErrorResults:
    """Error results are never truncated."""

    def test_error_not_truncated(self):
        guard = ToolResultGuard(max_chars=50)
        error = "Error: " + "x" * 200
        result = guard.cap("tool", "call_1", error)
        assert not result.was_truncated
        assert result.content == error

    def test_traceback_not_truncated(self):
        guard = ToolResultGuard(max_chars=50)
        error = "Traceback (most recent call last):\n" + "x" * 200
        result = guard.cap("tool", "call_1", error)
        assert not result.was_truncated


class TestToolNameNormalization:
    """Tool name validation."""

    def test_unknown_tool_name_preserved(self):
        guard = ToolResultGuard(max_chars=1000)
        result = guard.cap("", "call_1", "output")
        assert result.content == "output"  # Still processes even with empty name
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_tool_result_guard.py -v`
Expected: ImportError

### Step 3: Implement ToolResultGuard

Create `mypalclara/core/tool_result_guard.py`:

```python
"""Intelligent tool result size capping.

Replaces blind truncation with content-aware strategies:
- JSON: preserve structure, trim arrays from middle (keep first 3 + last 2)
- Text: 70/20 split (head/tail) with truncation marker
- Errors: never truncated
"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 50_000
HEAD_RATIO = 0.70
TAIL_RATIO = 0.20
JSON_KEEP_HEAD = 3
JSON_KEEP_TAIL = 2

ERROR_PREFIXES = ("Error:", "Traceback ", "Exception:", "FAILED", "error:")


@dataclass
class CappedResult:
    content: str
    was_truncated: bool
    original_size: int
    strategy: str  # "json" | "text_70_20" | "none"


class ToolResultGuard:
    def __init__(self, max_chars: int = DEFAULT_MAX_CHARS):
        self._max_chars = max_chars

    def cap(self, tool_name: str, tool_call_id: str, result: str) -> CappedResult:
        original_size = len(result)

        # Never truncate errors
        if self._is_error(result):
            return CappedResult(result, False, original_size, "none")

        if original_size <= self._max_chars:
            return CappedResult(result, False, original_size, "none")

        # Try JSON-aware truncation first
        json_result = self._try_json_truncation(result)
        if json_result is not None:
            logger.info(
                f"Tool result truncated (json): {tool_name} {original_size} -> {len(json_result)}"
            )
            return CappedResult(json_result, True, original_size, "json")

        # Fall back to 70/20 text split
        text_result = self._text_70_20(result)
        logger.info(
            f"Tool result truncated (text_70_20): {tool_name} {original_size} -> {len(text_result)}"
        )
        return CappedResult(text_result, True, original_size, "text_70_20")

    def _is_error(self, result: str) -> bool:
        return any(result.lstrip().startswith(p) for p in ERROR_PREFIXES)

    def _text_70_20(self, result: str) -> str:
        head_chars = int(self._max_chars * HEAD_RATIO)
        tail_chars = int(self._max_chars * TAIL_RATIO)
        head = result[:head_chars]
        tail = result[-tail_chars:] if tail_chars > 0 else ""
        marker = f"\n...[truncated: kept {head_chars}+{tail_chars} of {len(result)} chars]...\n"
        return head + marker + tail

    def _try_json_truncation(self, result: str) -> str | None:
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, ValueError):
            return None

        truncated = self._truncate_json_value(data)
        output = json.dumps(truncated, indent=2, default=str)

        if len(output) <= self._max_chars:
            marker = f"\n...[truncated: JSON arrays trimmed, original {len(result)} chars]..."
            return output + marker

        # JSON truncation wasn't enough, fall back to text
        return None

    def _truncate_json_value(self, value):
        if isinstance(value, list) and len(value) > JSON_KEEP_HEAD + JSON_KEEP_TAIL:
            head = value[:JSON_KEEP_HEAD]
            tail = value[-JSON_KEEP_TAIL:]
            return head + tail
        if isinstance(value, dict):
            return {k: self._truncate_json_value(v) for k, v in value.items()}
        return value
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_tool_result_guard.py -v`
Expected: All 8 tests PASS

### Step 5: Integrate into LLMOrchestrator

Modify `mypalclara/gateway/llm_orchestrator.py`:

At imports (line 16), add:
```python
from mypalclara.core.tool_result_guard import ToolResultGuard
```

In `__init__` (line 87), add:
```python
self._result_guard = ToolResultGuard()
```

Replace the truncation block at line 278:
```python
# OLD:
# if len(output) > MAX_TOOL_RESULT_CHARS:
#     output = self._truncate_output(output)

# NEW:
capped = self._result_guard.cap(tc.name, tc.id, output)
if capped.was_truncated:
    logger.info(f"Tool result capped ({capped.strategy}): {capped.original_size} -> {len(capped.content)}")
output = capped.content
```

Remove or deprecate `_truncate_output()` method at line 517.

### Step 6: Run all tests

Run: `poetry run pytest tests/ -v --timeout=30`
Expected: All tests PASS

### Step 7: Lint and commit

```bash
poetry run ruff check mypalclara/core/tool_result_guard.py tests/clara_core/test_tool_result_guard.py && poetry run ruff format mypalclara/core/tool_result_guard.py tests/clara_core/test_tool_result_guard.py
git add mypalclara/core/tool_result_guard.py tests/clara_core/test_tool_result_guard.py mypalclara/gateway/llm_orchestrator.py
git commit -m "feat: add intelligent tool result size capping (OpenClaw-inspired)

Replaces blind 50K char truncation with content-aware strategies:
JSON-aware (preserve structure, trim arrays), 70/20 text split
(keep head+tail), and error preservation (never truncate)."
```

---

## Task 3: Model Fallback with Cooldown Classification

**Files:**
- Create: `mypalclara/core/llm/failover.py`
- Create: `tests/clara_core/test_failover.py`
- Modify: `mypalclara/core/llm/config.py:19-65`
- Modify: `mypalclara/core/llm/providers/registry.py:44-85`

### Step 1: Write failing tests

Create `tests/clara_core/test_failover.py`:

```python
"""Tests for LLM provider failover with cooldown classification."""
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mypalclara.core.llm.failover import (
    FailoverReason,
    CooldownManager,
    ResilientProvider,
    classify_error,
)


class TestFailureClassification:
    """Classify errors into failover reasons."""

    def test_auth_error_401(self):
        err = Exception("HTTP 401 Unauthorized")
        assert classify_error(err) == FailoverReason.AUTH

    def test_auth_error_403(self):
        err = Exception("HTTP 403 Forbidden")
        assert classify_error(err) == FailoverReason.AUTH

    def test_rate_limit_429(self):
        err = Exception("HTTP 429 Too Many Requests")
        assert classify_error(err) == FailoverReason.RATE_LIMIT

    def test_context_overflow(self):
        err = Exception("maximum context length exceeded")
        assert classify_error(err) == FailoverReason.CONTEXT_OVERFLOW

    def test_transient_500(self):
        err = Exception("HTTP 500 Internal Server Error")
        assert classify_error(err) == FailoverReason.TRANSIENT

    def test_transient_timeout(self):
        err = TimeoutError("Connection timed out")
        assert classify_error(err) == FailoverReason.TRANSIENT

    def test_transient_connection(self):
        err = ConnectionError("Connection refused")
        assert classify_error(err) == FailoverReason.TRANSIENT

    def test_unknown_error(self):
        err = Exception("Something weird happened")
        assert classify_error(err) == FailoverReason.UNKNOWN


class TestCooldownManager:
    """Cooldown tracking with expiry."""

    def test_no_cooldown_initially(self):
        cm = CooldownManager()
        assert not cm.is_cooled_down("openrouter")

    def test_provider_cooldown(self):
        cm = CooldownManager()
        cm.set_cooldown("openrouter", None, 10.0, FailoverReason.AUTH)
        assert cm.is_cooled_down("openrouter")
        assert cm.is_cooled_down("openrouter", "claude-sonnet")

    def test_model_cooldown(self):
        cm = CooldownManager()
        cm.set_cooldown("openrouter", "claude-sonnet", 10.0, FailoverReason.RATE_LIMIT)
        assert cm.is_cooled_down("openrouter", "claude-sonnet")
        assert not cm.is_cooled_down("openrouter", "claude-haiku")

    def test_cooldown_expires(self):
        cm = CooldownManager()
        cm.set_cooldown("openrouter", None, 0.01, FailoverReason.AUTH)
        time.sleep(0.02)
        assert not cm.is_cooled_down("openrouter")

    def test_clear_cooldown(self):
        cm = CooldownManager()
        cm.set_cooldown("openrouter", None, 10.0, FailoverReason.AUTH)
        cm.clear("openrouter")
        assert not cm.is_cooled_down("openrouter")


class TestResilientProvider:
    """Retry and failover across providers."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        primary = AsyncMock()
        primary.complete = AsyncMock(return_value="response")
        primary.provider_name = "primary"
        primary.model_name = "model-a"

        provider = ResilientProvider(primary, [], CooldownManager())
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result == "response"

    @pytest.mark.asyncio
    async def test_failover_on_auth_error(self):
        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=Exception("HTTP 401 Unauthorized"))
        primary.provider_name = "primary"
        primary.model_name = "model-a"

        fallback = AsyncMock()
        fallback.complete = AsyncMock(return_value="fallback response")
        fallback.provider_name = "fallback"
        fallback.model_name = "model-b"

        provider = ResilientProvider(primary, [fallback], CooldownManager())
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result == "fallback response"

    @pytest.mark.asyncio
    async def test_context_overflow_not_retried(self):
        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=Exception("maximum context length exceeded"))
        primary.provider_name = "primary"
        primary.model_name = "model-a"

        fallback = AsyncMock()
        fallback.complete = AsyncMock(return_value="fallback")
        fallback.provider_name = "fallback"
        fallback.model_name = "model-b"

        provider = ResilientProvider(primary, [fallback], CooldownManager())
        with pytest.raises(Exception, match="context length"):
            await provider.complete([{"role": "user", "content": "hi"}])
        # Fallback should NOT have been called
        fallback.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        call_count = 0

        async def flaky_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("HTTP 500 Internal Server Error")
            return "recovered"

        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=flaky_complete)
        primary.provider_name = "primary"
        primary.model_name = "model-a"

        provider = ResilientProvider(primary, [], CooldownManager(), max_retries=3, base_delay=0.01)
        result = await provider.complete([{"role": "user", "content": "hi"}])
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_all_providers_fail(self):
        primary = AsyncMock()
        primary.complete = AsyncMock(side_effect=Exception("HTTP 401"))
        primary.provider_name = "primary"
        primary.model_name = "model-a"

        fallback = AsyncMock()
        fallback.complete = AsyncMock(side_effect=Exception("HTTP 401"))
        fallback.provider_name = "fallback"
        fallback.model_name = "model-b"

        provider = ResilientProvider(primary, [fallback], CooldownManager())
        with pytest.raises(Exception):
            await provider.complete([{"role": "user", "content": "hi"}])
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_failover.py -v`
Expected: ImportError

### Step 3: Implement failover module

Create `mypalclara/core/llm/failover.py`:

```python
"""LLM provider failover with cooldown classification.

Failure types:
- AUTH: HTTP 401/403, billing errors -> cooldown entire provider (10 min)
- RATE_LIMIT: HTTP 429 -> backoff, try sibling model (30s cooldown)
- CONTEXT_OVERFLOW: context length exceeded -> rethrow immediately
- TRANSIENT: HTTP 500/502/503, timeout, connection -> retry with backoff (max 3)
- UNKNOWN: anything else -> treat as transient
"""

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class FailoverReason(Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT_OVERFLOW = "context_overflow"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


AUTH_PATTERNS = re.compile(r"(401|403|unauthorized|forbidden|billing|payment)", re.IGNORECASE)
RATE_LIMIT_PATTERNS = re.compile(r"(429|too many requests|rate.?limit)", re.IGNORECASE)
CONTEXT_PATTERNS = re.compile(r"(context.?length|token.?limit|maximum.?context|too.?long)", re.IGNORECASE)
TRANSIENT_PATTERNS = re.compile(r"(500|502|503|504|internal.?server|bad.?gateway|service.?unavailable|overloaded)", re.IGNORECASE)


def classify_error(error: Exception) -> FailoverReason:
    msg = str(error)

    if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
        return FailoverReason.TRANSIENT
    if isinstance(error, ConnectionError):
        return FailoverReason.TRANSIENT
    if CONTEXT_PATTERNS.search(msg):
        return FailoverReason.CONTEXT_OVERFLOW
    if AUTH_PATTERNS.search(msg):
        return FailoverReason.AUTH
    if RATE_LIMIT_PATTERNS.search(msg):
        return FailoverReason.RATE_LIMIT
    if TRANSIENT_PATTERNS.search(msg):
        return FailoverReason.TRANSIENT

    return FailoverReason.UNKNOWN


@dataclass
class _CooldownEntry:
    provider: str
    model: str | None
    expires_at: float
    reason: FailoverReason


class CooldownManager:
    def __init__(self):
        self._entries: list[_CooldownEntry] = []

    def is_cooled_down(self, provider: str, model: str | None = None) -> bool:
        now = time.monotonic()
        self._entries = [e for e in self._entries if e.expires_at > now]
        for entry in self._entries:
            if entry.provider == provider:
                if entry.model is None or entry.model == model:
                    return True
        return False

    def set_cooldown(self, provider: str, model: str | None, duration_s: float, reason: FailoverReason) -> None:
        expires_at = time.monotonic() + duration_s
        self._entries.append(_CooldownEntry(provider, model, expires_at, reason))
        scope = f"{provider}/{model}" if model else provider
        logger.warning(f"Cooldown set: {scope} for {duration_s}s ({reason.value})")

    def clear(self, provider: str, model: str | None = None) -> None:
        if model is None:
            self._entries = [e for e in self._entries if e.provider != provider]
        else:
            self._entries = [
                e for e in self._entries
                if not (e.provider == provider and e.model == model)
            ]


AUTH_COOLDOWN_S = 600.0  # 10 minutes
RATE_LIMIT_COOLDOWN_S = 30.0


class ResilientProvider:
    """Wraps provider chain with retry, backoff, and failover.

    Transparent drop-in for LLMProvider — same interface.
    """

    def __init__(
        self,
        primary,
        fallbacks: list,
        cooldowns: CooldownManager,
        max_retries: int = 3,
        base_delay: float = 1.0,
    ):
        self._primary = primary
        self._fallbacks = fallbacks
        self._cooldowns = cooldowns
        self._max_retries = max_retries
        self._base_delay = base_delay

    @property
    def provider_name(self) -> str:
        return self._primary.provider_name

    @property
    def model_name(self) -> str:
        return self._primary.model_name

    async def complete(self, messages, **kwargs):
        return await self._call_with_failover("complete", messages, **kwargs)

    async def complete_with_tools(self, messages, tools, **kwargs):
        return await self._call_with_failover("complete_with_tools", messages, tools, **kwargs)

    async def stream(self, messages, **kwargs):
        return await self._call_with_failover("stream", messages, **kwargs)

    async def stream_with_tools(self, messages, tools, **kwargs):
        return await self._call_with_failover("stream_with_tools", messages, tools, **kwargs)

    async def _call_with_failover(self, method_name: str, *args, **kwargs):
        providers = [self._primary] + self._fallbacks
        last_error = None

        for provider in providers:
            pname = getattr(provider, "provider_name", "unknown")
            mname = getattr(provider, "model_name", "unknown")

            if self._cooldowns.is_cooled_down(pname, mname):
                logger.info(f"Skipping cooled-down provider: {pname}/{mname}")
                continue

            for attempt in range(self._max_retries):
                try:
                    method = getattr(provider, method_name)
                    return await method(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    reason = classify_error(e)
                    logger.warning(
                        f"LLM call failed ({pname}/{mname}): {reason.value} "
                        f"(attempt {attempt + 1}/{self._max_retries}): {e}"
                    )

                    if reason == FailoverReason.CONTEXT_OVERFLOW:
                        raise

                    if reason == FailoverReason.AUTH:
                        self._cooldowns.set_cooldown(pname, None, AUTH_COOLDOWN_S, reason)
                        break  # Skip to next provider

                    if reason == FailoverReason.RATE_LIMIT:
                        self._cooldowns.set_cooldown(pname, mname, RATE_LIMIT_COOLDOWN_S, reason)
                        break  # Skip to next provider

                    # Transient / Unknown: retry with backoff
                    if attempt < self._max_retries - 1:
                        delay = self._base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                        await asyncio.sleep(delay)

        if last_error:
            raise last_error
        raise RuntimeError("No providers available (all cooled down)")
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_failover.py -v`
Expected: All 13 tests PASS

### Step 5: Add fallback_configs to LLMConfig

Modify `mypalclara/core/llm/config.py` at line 65 (end of fields):
```python
    # Failover
    fallback_configs: "list[LLMConfig]" = field(default_factory=list)
```

Add to `from_env()` (after the main config is built, before return), around line 215:
```python
    # Load fallback providers from env
    fallbacks = []
    for i in range(1, 4):  # Up to 3 fallbacks
        fb_provider = os.getenv(f"LLM_FALLBACK_{i}_PROVIDER")
        fb_model = os.getenv(f"LLM_FALLBACK_{i}_MODEL")
        if fb_provider and fb_model:
            fallbacks.append(LLMConfig(provider=fb_provider, model=fb_model))
    config.fallback_configs = fallbacks
```

### Step 6: Integrate ResilientProvider into ProviderRegistry

Modify `mypalclara/core/llm/providers/registry.py`:

Add import at top:
```python
from mypalclara.core.llm.failover import ResilientProvider, CooldownManager
```

Add class-level cooldown manager:
```python
class ProviderRegistry:
    _cooldowns: CooldownManager = CooldownManager()
```

In `get_provider()` (line 44), wrap the returned provider:
```python
    @classmethod
    def get_provider(cls, provider_type: str = "langchain", config: "LLMConfig | None" = None) -> LLMProvider:
        # ... existing provider creation logic ...
        provider = ...  # existing return value

        # Wrap with resilient provider if fallbacks configured
        if config and config.fallback_configs:
            fallback_providers = []
            for fb_config in config.fallback_configs:
                fb_provider = cls._create_provider(provider_type, fb_config)
                fallback_providers.append(fb_provider)
            return ResilientProvider(provider, fallback_providers, cls._cooldowns)

        return provider
```

### Step 7: Run all tests

Run: `poetry run pytest tests/ -v --timeout=30`
Expected: All tests PASS

### Step 8: Lint and commit

```bash
poetry run ruff check mypalclara/core/llm/failover.py tests/clara_core/test_failover.py && poetry run ruff format mypalclara/core/llm/failover.py tests/clara_core/test_failover.py
git add mypalclara/core/llm/failover.py tests/clara_core/test_failover.py mypalclara/core/llm/config.py mypalclara/core/llm/providers/registry.py
git commit -m "feat: add LLM provider failover with cooldown classification (OpenClaw-inspired)

Classifies failures (auth, rate limit, context overflow, transient),
applies appropriate cooldowns, retries with exponential backoff,
and fails over to configured fallback providers. Context overflow
errors rethrow immediately."
```

---

## Task 4: Compositional Prompt Builder

**Files:**
- Modify: `mypalclara/core/prompt_builder.py` (major refactor)
- Create: `tests/clara_core/test_prompt_builder_v2.py`

### Step 1: Write failing tests for new section builder pattern

Create `tests/clara_core/test_prompt_builder_v2.py`:

```python
"""Tests for compositional prompt builder with modes and section budgets."""
import pytest
from unittest.mock import patch, MagicMock
from mypalclara.core.prompt_builder import PromptBuilder, PromptMode


class TestPromptModes:
    """Three prompt modes: full, minimal, none."""

    def _make_builder(self):
        return PromptBuilder(agent_id="test", llm_callable=None)

    @patch("mypalclara.core.prompt_builder.build_worm_persona", return_value="You are Clara.")
    def test_none_mode_minimal_output(self, mock_worm):
        builder = self._make_builder()
        messages = builder.build_prompt(
            user_mems=[], proj_mems=[], thread_summary="",
            recent_msgs=[], user_message="hi",
            mode=PromptMode.NONE,
        )
        assert len(messages) >= 2  # system + user
        assert "Clara" in messages[0].content

    @patch("mypalclara.core.prompt_builder.build_worm_persona", return_value="You are Clara.")
    def test_full_mode_has_all_sections(self, mock_worm):
        builder = self._make_builder()
        messages = builder.build_prompt(
            user_mems=[{"memory": "likes coffee"}],
            proj_mems=[],
            thread_summary="",
            recent_msgs=[],
            user_message="hi",
            mode=PromptMode.FULL,
        )
        # Full mode should have system message with persona
        assert any("Clara" in m.content for m in messages if hasattr(m, 'content'))


class TestSectionBudgets:
    """Per-section character budget enforcement."""

    def _make_builder(self):
        return PromptBuilder(agent_id="test", llm_callable=None)

    @patch("mypalclara.core.prompt_builder.build_worm_persona", return_value="You are Clara.")
    def test_long_section_truncated(self, mock_worm):
        builder = self._make_builder()
        # Provide extremely long user memories to test budget
        huge_mems = [{"memory": "x" * 50000}]
        messages = builder.build_prompt(
            user_mems=huge_mems, proj_mems=[], thread_summary="",
            recent_msgs=[], user_message="hi",
            mode=PromptMode.FULL,
        )
        # System message content should be bounded
        total_system = sum(len(m.content) for m in messages if hasattr(m, 'role') and m.role == "system")
        assert total_system < 250_000  # Well under unlimited


class TestSectionBuilders:
    """Individual section builder methods."""

    def _make_builder(self):
        return PromptBuilder(agent_id="test", llm_callable=None)

    def test_build_datetime_includes_timestamp(self):
        builder = self._make_builder()
        lines = builder._build_datetime()
        assert len(lines) > 0
        assert any("202" in line for line in lines)  # Contains year

    def test_build_runtime_includes_agent_id(self):
        builder = self._make_builder()
        lines = builder._build_runtime()
        assert any("test" in line for line in lines)
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_prompt_builder_v2.py -v`
Expected: ImportError for `PromptMode`, or AttributeError for new methods

### Step 3: Refactor PromptBuilder

Modify `mypalclara/core/prompt_builder.py`:

Add at top of file:
```python
from enum import Enum
from datetime import datetime, timezone
import platform

class PromptMode(Enum):
    FULL = "full"
    MINIMAL = "minimal"
    NONE = "none"

SECTION_MAX_CHARS = 10_000
TOTAL_SYSTEM_MAX_CHARS = 200_000
```

Refactor the `PromptBuilder` class to add section builders and mode support. The existing `build_prompt()` signature gains `mode: PromptMode = PromptMode.FULL` parameter. Internal logic switches on mode:

- `NONE`: Returns `[SystemMessage("You are {BOT_NAME}, an AI assistant."), UserMessage(user_message)]`
- `MINIMAL`: Builds sections 1 (identity), 2 (tooling), 7 (workspace), 12 (runtime) only
- `FULL`: Builds all 12 sections

Each section is a `_build_*()` method returning `list[str]`. Add:
- `_build_datetime()`: returns `["## Current Date & Time", f"Current: {datetime.now(timezone.utc).isoformat()}"]`
- `_build_runtime()`: returns `["## Runtime", f"Agent: {self._agent_id}, OS: {platform.system()}, Python: {platform.python_version()}"]`
- `_apply_section_budget(lines, max_chars)`: static method that applies 70/20 truncation to joined section text

Preserve all existing functionality — the refactor wraps existing code into section methods without changing behavior for the default `FULL` mode.

### Step 4: Run all tests (both old and new)

Run: `poetry run pytest tests/clara_core/test_prompt_builder.py tests/clara_core/test_prompt_builder_v2.py -v`
Expected: All tests PASS (old tests confirm no regression, new tests confirm new features)

### Step 5: Lint and commit

```bash
poetry run ruff check mypalclara/core/prompt_builder.py tests/clara_core/test_prompt_builder_v2.py && poetry run ruff format mypalclara/core/prompt_builder.py tests/clara_core/test_prompt_builder_v2.py
git add mypalclara/core/prompt_builder.py tests/clara_core/test_prompt_builder_v2.py
git commit -m "refactor: compositional prompt builder with modes and section budgets (OpenClaw-inspired)

Three prompt modes (full/minimal/none), section builder pattern,
per-section character budgets with 70/20 truncation. Preserves
existing WORM persona and all current behavior."
```

---

## Task 5: Workspace Files

**Files:**
- Create: `mypalclara/core/workspace_loader.py`
- Create: `mypalclara/workspace/SOUL.md`
- Create: `mypalclara/workspace/IDENTITY.md`
- Create: `mypalclara/workspace/USER.md`
- Create: `mypalclara/workspace/AGENTS.md`
- Create: `mypalclara/workspace/TOOLS.md`
- Create: `mypalclara/workspace/MEMORY.md`
- Create: `tests/clara_core/test_workspace_loader.py`
- Modify: `mypalclara/core/prompt_builder.py`

### Step 1: Write failing tests

Create `tests/clara_core/test_workspace_loader.py`:

```python
"""Tests for workspace file loading with budget management."""
import pytest
from pathlib import Path
from mypalclara.core.workspace_loader import WorkspaceLoader, WorkspaceFile


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with test files."""
    (tmp_path / "SOUL.md").write_text("Be warm and helpful.")
    (tmp_path / "IDENTITY.md").write_text("- **Name:** TestBot\n- **Emoji:** sparkle\n- **Vibe:** friendly")
    (tmp_path / "USER.md").write_text("Joshua likes coffee.")
    (tmp_path / "AGENTS.md").write_text("Always be concise.")
    return tmp_path


class TestWorkspaceLoading:
    def test_load_all_files(self, tmp_workspace):
        loader = WorkspaceLoader()
        files = loader.load(tmp_workspace, mode="full")
        names = [f.filename for f in files]
        assert "SOUL.md" in names
        assert "IDENTITY.md" in names
        assert "USER.md" in names
        assert "AGENTS.md" in names

    def test_missing_files_skipped(self, tmp_path):
        loader = WorkspaceLoader()
        files = loader.load(tmp_path, mode="full")
        assert len(files) == 0

    def test_minimal_mode_subset(self, tmp_workspace):
        # Also add TOOLS.md and MEMORY.md
        (tmp_workspace / "TOOLS.md").write_text("Tool notes here.")
        (tmp_workspace / "MEMORY.md").write_text("Remember this.")
        loader = WorkspaceLoader()
        files = loader.load(tmp_workspace, mode="minimal")
        names = [f.filename for f in files]
        assert "SOUL.md" in names
        assert "IDENTITY.md" in names
        # TOOLS.md and MEMORY.md excluded in minimal
        assert "TOOLS.md" not in names
        assert "MEMORY.md" not in names


class TestBudgetManagement:
    def test_large_file_truncated(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("x" * 30_000)
        loader = WorkspaceLoader(per_file_max=20_000)
        files = loader.load(tmp_path)
        soul = [f for f in files if f.filename == "SOUL.md"][0]
        assert soul.was_truncated
        assert len(soul.content) < 25_000  # 20K + marker

    def test_total_budget_enforced(self, tmp_path):
        for name in ["SOUL.md", "AGENTS.md", "USER.md"]:
            (tmp_path / name).write_text("y" * 60_000)
        loader = WorkspaceLoader(per_file_max=60_000, total_max=100_000)
        files = loader.load(tmp_path)
        total = sum(len(f.content) for f in files)
        assert total <= 110_000  # 100K + markers


class TestIdentityParsing:
    def test_parse_structured_fields(self, tmp_workspace):
        loader = WorkspaceLoader()
        files = loader.load(tmp_workspace)
        identity = [f for f in files if f.filename == "IDENTITY.md"][0]
        assert identity.structured_fields is not None
        assert identity.structured_fields.get("name") == "TestBot"
        assert identity.structured_fields.get("emoji") == "sparkle"
        assert identity.structured_fields.get("vibe") == "friendly"
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_workspace_loader.py -v`
Expected: ImportError

### Step 3: Implement WorkspaceLoader

Create `mypalclara/core/workspace_loader.py`:

```python
"""Workspace file loader with budget management.

Loads user-editable markdown files from workspace directories,
applies per-file and total character budgets, and parses
structured fields from IDENTITY.md.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PER_FILE_MAX = 20_000
DEFAULT_TOTAL_MAX = 150_000
HEAD_RATIO = 0.70
TAIL_RATIO = 0.20

# Files loaded in full mode
FULL_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md", "TOOLS.md", "MEMORY.md"]
# Files loaded in minimal mode (sub-agents)
MINIMAL_FILES = ["SOUL.md", "IDENTITY.md", "USER.md", "AGENTS.md"]


@dataclass
class WorkspaceFile:
    filename: str
    content: str
    was_truncated: bool = False
    structured_fields: dict | None = None


class WorkspaceLoader:
    def __init__(
        self,
        per_file_max: int = DEFAULT_PER_FILE_MAX,
        total_max: int = DEFAULT_TOTAL_MAX,
    ):
        self._per_file_max = per_file_max
        self._total_max = total_max

    def load(self, workspace_dir: Path, mode: str = "full") -> list[WorkspaceFile]:
        filenames = FULL_FILES if mode == "full" else MINIMAL_FILES
        files: list[WorkspaceFile] = []
        total_chars = 0

        for filename in filenames:
            path = workspace_dir / filename
            if not path.exists():
                continue

            content = path.read_text(encoding="utf-8", errors="replace")
            was_truncated = False

            # Per-file budget
            if len(content) > self._per_file_max:
                content = self._truncate_70_20(content, self._per_file_max, filename)
                was_truncated = True

            # Total budget
            if total_chars + len(content) > self._total_max:
                remaining = self._total_max - total_chars
                if remaining <= 0:
                    logger.info(f"Total budget exhausted, skipping {filename}")
                    break
                content = self._truncate_70_20(content, remaining, filename)
                was_truncated = True

            total_chars += len(content)

            structured = None
            if filename == "IDENTITY.md":
                structured = self._parse_identity(content)

            files.append(WorkspaceFile(filename, content, was_truncated, structured))

        return files

    def _truncate_70_20(self, content: str, max_chars: int, filename: str) -> str:
        head_chars = int(max_chars * HEAD_RATIO)
        tail_chars = int(max_chars * TAIL_RATIO)
        head = content[:head_chars]
        tail = content[-tail_chars:] if tail_chars > 0 else ""
        marker = f"\n[...truncated, see {filename} for full content ({len(content)} chars)...]\n"
        return head + marker + tail

    def _parse_identity(self, content: str) -> dict:
        fields = {}
        pattern = re.compile(r"-\s*\*\*(\w+):\*\*\s*(.+)")
        for match in pattern.finditer(content):
            key = match.group(1).lower()
            value = match.group(2).strip()
            fields[key] = value
        return fields
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_workspace_loader.py -v`
Expected: All 7 tests PASS

### Step 5: Create default workspace templates

Create `mypalclara/workspace/SOUL.md` — copy content from `mypalclara/config/personality.md` (the existing personality-specific text + universal instructions).

Create `mypalclara/workspace/IDENTITY.md`:
```markdown
- **Name:** Clara
- **Emoji:** sparkle
- **Vibe:** warm, thoughtful, genuinely helpful
- **Avatar:** default
```

Create `mypalclara/workspace/USER.md`:
```markdown
# User Profile

Edit this file to tell Clara about yourself.

- **Name:** (your name)
- **Timezone:** (your timezone)
- **Preferences:** (anything Clara should know)
```

Create `mypalclara/workspace/AGENTS.md` — extract universal instructions from `config/personality.md`.

Create `mypalclara/workspace/TOOLS.md`:
```markdown
# Tool Configuration Notes

Add tool-specific notes here. Clara will read this at the start of each conversation.
```

Create `mypalclara/workspace/MEMORY.md`:
```markdown
# Long-Term Memory

Add important things for Clara to remember here. Unlike Rook memories (which Clara manages automatically), this file is for things you want to explicitly persist.
```

### Step 6: Integrate into PromptBuilder

Modify `mypalclara/core/prompt_builder.py` — add `_build_workspace_files()` section builder that calls `WorkspaceLoader.load()` and formats each file as:

```
## {filename}

{content}
```

Wire into section 10 of the compositional builder.

### Step 7: Run all tests

Run: `poetry run pytest tests/ -v --timeout=30`
Expected: All tests PASS

### Step 8: Lint and commit

```bash
poetry run ruff check mypalclara/core/workspace_loader.py tests/clara_core/test_workspace_loader.py && poetry run ruff format mypalclara/core/workspace_loader.py tests/clara_core/test_workspace_loader.py
git add mypalclara/core/workspace_loader.py tests/clara_core/test_workspace_loader.py mypalclara/workspace/ mypalclara/core/prompt_builder.py
git commit -m "feat: add workspace files with budget management (OpenClaw-inspired)

User-editable markdown files (SOUL.md, IDENTITY.md, USER.md, AGENTS.md,
TOOLS.md, MEMORY.md) injected into prompts with per-file (20K) and
total (150K) character budgets. 70/20 truncation strategy. IDENTITY.md
parsed for structured fields."
```

---

## Task 6: Human-Readable Tool Summaries in Prompts

**Files:**
- Create: `mypalclara/core/tool_summaries.py`
- Create: `tests/clara_core/test_tool_summaries.py`
- Modify: `mypalclara/core/prompt_builder.py`

### Step 1: Write failing tests

Create `tests/clara_core/test_tool_summaries.py`:

```python
"""Tests for human-readable tool summary generation."""
import pytest
from mypalclara.core.tool_summaries import build_tool_summary_section


def _make_tool(name: str, desc: str) -> dict:
    return {"name": name, "description": desc, "parameters": {}}


class TestToolSummaries:
    def test_basic_summary(self):
        tools = [_make_tool("memory_search", "Search memories by semantic query")]
        lines = build_tool_summary_section(tools)
        text = "\n".join(lines)
        assert "memory_search" in text
        assert "Search memories" in text

    def test_grouping_by_prefix(self):
        tools = [
            _make_tool("mcp__github__list_issues", "List GitHub issues"),
            _make_tool("mcp__github__create_issue", "Create a GitHub issue"),
            _make_tool("memory_search", "Search memories"),
        ]
        lines = build_tool_summary_section(tools)
        text = "\n".join(lines)
        assert "MCP Tools:" in text or "mcp__github" in text

    def test_budget_enforcement(self):
        tools = [_make_tool(f"tool_{i}", f"Description for tool {i} " * 20) for i in range(100)]
        lines = build_tool_summary_section(tools, max_chars=500)
        text = "\n".join(lines)
        assert len(text) <= 600  # 500 + overflow message
        assert "more tools" in text

    def test_description_truncated_at_80_chars(self):
        tools = [_make_tool("tool_a", "A" * 200)]
        lines = build_tool_summary_section(tools)
        text = "\n".join(lines)
        # Should not contain the full 200 chars
        assert "A" * 81 not in text

    def test_empty_tools(self):
        lines = build_tool_summary_section([])
        assert len(lines) == 0
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_tool_summaries.py -v`
Expected: ImportError

### Step 3: Implement tool summaries

Create `mypalclara/core/tool_summaries.py`:

```python
"""Human-readable tool summary generation for system prompts.

Generates a concise tool listing grouped by prefix, supplementing
native API tool schemas with a high-level orientation map.
"""

import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 5000
DESC_MAX_CHARS = 80

# Core tools ordered by frequency of use
CORE_TOOL_ORDER = [
    "memory_search", "memory_store", "web_search", "web_fetch",
    "code_execute", "file_read", "file_write", "browser",
]


def build_tool_summary_section(
    tools: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[str]:
    if not tools:
        return []

    groups: dict[str, list[tuple[str, str]]] = {}

    for tool in tools:
        name = tool.get("name", "")
        desc = tool.get("description", "")

        # Truncate description
        desc = _truncate_desc(desc)

        # Group by prefix
        group = _get_group(name)
        if group not in groups:
            groups[group] = []
        groups[group].append((name, desc))

    # Build output
    lines = ["## Available Tools", "Tool names are case-sensitive. Call tools exactly as listed.", ""]

    total_chars = 0
    remaining_count = 0

    # Core tools first, then alphabetical
    sorted_groups = sorted(groups.keys(), key=lambda g: (0 if g == "Core" else 1, g))

    for group in sorted_groups:
        group_lines = [f"{group} Tools:"]
        for name, desc in groups[group]:
            line = f"- {name}: {desc}"
            group_lines.append(line)
        group_lines.append("")

        group_text = "\n".join(group_lines)
        if total_chars + len(group_text) > max_chars:
            remaining_count += len(groups[group])
            continue

        lines.extend(group_lines)
        total_chars += len(group_text)

    if remaining_count > 0:
        lines.append(f"...and {remaining_count} more tools available")

    return lines


def _truncate_desc(desc: str) -> str:
    # Take first sentence
    for sep in [". ", ".\n", "\n"]:
        if sep in desc:
            desc = desc[:desc.index(sep)]
            break
    if len(desc) > DESC_MAX_CHARS:
        desc = desc[:DESC_MAX_CHARS - 3] + "..."
    return desc


def _get_group(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 2:
            return f"MCP ({parts[1]})"
        return "MCP"
    if name.startswith("subagent_"):
        return "Subagent"
    return "Core"
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_tool_summaries.py -v`
Expected: All 5 tests PASS

### Step 5: Integrate into PromptBuilder

Modify `mypalclara/core/prompt_builder.py` — add `_build_tooling()` section builder that calls `build_tool_summary_section(tools)`. Wire into section 2.

### Step 6: Run all tests, lint, commit

```bash
poetry run pytest tests/ -v --timeout=30
poetry run ruff check mypalclara/core/tool_summaries.py tests/clara_core/test_tool_summaries.py && poetry run ruff format mypalclara/core/tool_summaries.py tests/clara_core/test_tool_summaries.py
git add mypalclara/core/tool_summaries.py tests/clara_core/test_tool_summaries.py mypalclara/core/prompt_builder.py
git commit -m "feat: add human-readable tool summaries in system prompts (OpenClaw-inspired)

Concise tool listing grouped by prefix (Core, MCP, Subagent),
injected into system prompt as orientation supplement to native
tool schemas. Per-tool descriptions capped at 80 chars, total
section budgeted at 5K chars."
```

---

## Task 7: Context Compaction (Progressive Summarization)

**Files:**
- Create: `mypalclara/core/context_compactor.py`
- Create: `tests/clara_core/test_context_compactor.py`
- Modify: `mypalclara/gateway/llm_orchestrator.py`

### Step 1: Write failing tests

Create `tests/clara_core/test_context_compactor.py`:

```python
"""Tests for progressive context compaction."""
import pytest
from unittest.mock import AsyncMock, patch
from mypalclara.core.context_compactor import ContextCompactor
from mypalclara.core.llm.messages import SystemMessage, UserMessage, AssistantMessage


def _make_messages(count: int, chars_each: int = 500) -> list:
    msgs = [SystemMessage(content="You are Clara.")]
    for i in range(count):
        if i % 2 == 0:
            msgs.append(UserMessage(content=f"User message {i}: " + "x" * chars_each))
        else:
            msgs.append(AssistantMessage(content=f"Assistant message {i}: " + "y" * chars_each))
    msgs.append(UserMessage(content="Current message"))
    return msgs


class TestCompactionDecision:
    """Should compact only when over budget."""

    @pytest.mark.asyncio
    async def test_no_compaction_under_budget(self):
        compactor = ContextCompactor(budget_ratio=0.6)
        messages = _make_messages(5, chars_each=100)
        result = await compactor.compact_if_needed(messages, budget_tokens=100_000)
        assert not result.was_compacted
        assert result.messages == messages

    @pytest.mark.asyncio
    async def test_compaction_over_budget(self):
        compactor = ContextCompactor(budget_ratio=0.6)
        messages = _make_messages(50, chars_each=2000)

        with patch.object(compactor, "_summarize_chunk", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = "Summary of conversation chunk."
            result = await compactor.compact_if_needed(messages, budget_tokens=5_000)

        assert result.was_compacted
        assert len(result.messages) < len(messages)
        assert result.tokens_saved > 0


class TestCompactionPreservation:
    """System messages and recent messages preserved."""

    @pytest.mark.asyncio
    async def test_system_message_preserved(self):
        compactor = ContextCompactor(budget_ratio=0.6)
        messages = _make_messages(50, chars_each=2000)

        with patch.object(compactor, "_summarize_chunk", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = "Summary."
            result = await compactor.compact_if_needed(messages, budget_tokens=5_000)

        # First message should still be system
        assert isinstance(result.messages[0], SystemMessage)
        assert "Clara" in result.messages[0].content

    @pytest.mark.asyncio
    async def test_current_message_preserved(self):
        compactor = ContextCompactor(budget_ratio=0.6)
        messages = _make_messages(50, chars_each=2000)

        with patch.object(compactor, "_summarize_chunk", new_callable=AsyncMock) as mock_sum:
            mock_sum.return_value = "Summary."
            result = await compactor.compact_if_needed(messages, budget_tokens=5_000)

        # Last message should be "Current message"
        assert "Current message" in result.messages[-1].content


class TestFallback:
    """Falls back to drop-oldest if summarization fails."""

    @pytest.mark.asyncio
    async def test_fallback_on_summarization_error(self):
        compactor = ContextCompactor(budget_ratio=0.6)
        messages = _make_messages(50, chars_each=2000)

        with patch.object(compactor, "_summarize_chunk", new_callable=AsyncMock) as mock_sum:
            mock_sum.side_effect = Exception("LLM error")
            result = await compactor.compact_if_needed(messages, budget_tokens=5_000)

        # Should still compact (via fallback), not crash
        assert result.was_compacted
        assert len(result.messages) < len(messages)
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_context_compactor.py -v`
Expected: ImportError

### Step 3: Implement ContextCompactor

Create `mypalclara/core/context_compactor.py`:

```python
"""Progressive context compaction via multi-stage summarization.

When conversation history exceeds the context budget, old messages
are summarized rather than dropped. Falls back to drop-oldest if
summarization fails.
"""

import logging
from dataclasses import dataclass
from mypalclara.core.token_counter import count_tokens, count_message_tokens
from mypalclara.core.llm.messages import SystemMessage, UserMessage, AssistantMessage

logger = logging.getLogger(__name__)

SAFETY_MARGIN = 0.80  # 20% buffer for tokenizer inaccuracy
RECENT_KEEP_RATIO = 0.40  # Keep most recent 40% of messages untouched

MERGE_PROMPT = """Merge these conversation summaries into a single cohesive summary.
MUST PRESERVE:
- Active tasks and their current status
- The last thing the user requested
- Decisions made and their rationale
- Open questions and constraints
- File paths, URLs, and identifiers
PRIORITIZE recent context over older history.
Keep the summary concise but complete."""


@dataclass
class CompactionResult:
    messages: list
    was_compacted: bool
    tokens_saved: int = 0
    summary_tokens: int = 0


class ContextCompactor:
    def __init__(self, budget_ratio: float = 0.6, llm_callable=None):
        self._budget_ratio = budget_ratio
        self._llm_callable = llm_callable

    async def compact_if_needed(self, messages: list, budget_tokens: int) -> CompactionResult:
        current_tokens = count_message_tokens(messages)
        threshold = int(budget_tokens * self._budget_ratio * SAFETY_MARGIN)

        if current_tokens <= threshold:
            return CompactionResult(messages, False)

        logger.info(f"Context compaction triggered: {current_tokens} tokens > {threshold} threshold")

        try:
            return await self._compact_with_summarization(messages, budget_tokens)
        except Exception as e:
            logger.warning(f"Summarization failed, falling back to drop-oldest: {e}")
            return self._compact_drop_oldest(messages, budget_tokens)

    async def _compact_with_summarization(self, messages: list, budget_tokens: int) -> CompactionResult:
        # Separate system messages, history, and current message
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        history = [m for m in messages if not isinstance(m, SystemMessage)]

        if not history:
            return CompactionResult(messages, False)

        current_msg = history[-1]
        history = history[:-1]

        # Keep recent 40%
        keep_count = max(2, int(len(history) * RECENT_KEEP_RATIO))
        to_compact = history[:-keep_count] if keep_count < len(history) else []
        to_keep = history[-keep_count:]

        if not to_compact:
            return CompactionResult(messages, False)

        # Summarize in chunks
        chunk_size = max(5, len(to_compact) // 3)
        chunks = [to_compact[i:i + chunk_size] for i in range(0, len(to_compact), chunk_size)]

        summaries = []
        for chunk in chunks:
            summary = await self._summarize_chunk(chunk)
            summaries.append(summary)

        # Merge summaries
        if len(summaries) > 1 and self._llm_callable:
            merged = await self._merge_summaries(summaries)
        else:
            merged = "\n\n".join(summaries)

        # Build compacted messages
        summary_msg = SystemMessage(content=f"## Conversation Summary\n{merged}")
        result_messages = system_msgs + [summary_msg] + to_keep + [current_msg]

        original_tokens = count_message_tokens(messages)
        new_tokens = count_message_tokens(result_messages)
        summary_tokens = count_tokens(merged)

        logger.info(f"Compacted: {original_tokens} -> {new_tokens} tokens (saved {original_tokens - new_tokens})")

        return CompactionResult(
            result_messages,
            True,
            tokens_saved=original_tokens - new_tokens,
            summary_tokens=summary_tokens,
        )

    async def _summarize_chunk(self, messages: list) -> str:
        if not self._llm_callable:
            # Fallback: extract key content
            lines = []
            for msg in messages:
                content = getattr(msg, "content", str(msg))
                if content:
                    # Strip tool result details for security
                    if "<untrusted_" in content:
                        content = "[tool result omitted for compaction]"
                    lines.append(content[:200])
            return "Previous context: " + " | ".join(lines)

        # Use LLM to summarize
        prompt = (
            "Summarize this conversation chunk concisely. Preserve key decisions, "
            "file paths, identifiers, and the user's intent:\n\n"
        )
        for msg in messages:
            content = getattr(msg, "content", str(msg))
            role = getattr(msg, "role", "unknown")
            if "<untrusted_" in content:
                content = "[tool result]"
            prompt += f"{role}: {content[:500]}\n"

        return await self._llm_callable(prompt)

    async def _merge_summaries(self, summaries: list[str]) -> str:
        prompt = MERGE_PROMPT + "\n\n" + "\n---\n".join(summaries)
        return await self._llm_callable(prompt)

    def _compact_drop_oldest(self, messages: list, budget_tokens: int) -> CompactionResult:
        """Fallback: drop oldest non-system messages."""
        system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
        history = [m for m in messages if not isinstance(m, SystemMessage)]

        if not history:
            return CompactionResult(messages, False)

        current_msg = history[-1]
        history = history[:-1]

        # Drop from oldest until under budget
        target = int(budget_tokens * self._budget_ratio * SAFETY_MARGIN)
        while history and count_message_tokens(system_msgs + history + [current_msg]) > target:
            history.pop(0)

        original_tokens = count_message_tokens(messages)
        result = system_msgs + history + [current_msg]
        new_tokens = count_message_tokens(result)

        return CompactionResult(result, True, tokens_saved=original_tokens - new_tokens)
```

### Step 4: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_context_compactor.py -v`
Expected: All 5 tests PASS

### Step 5: Integrate into LLMOrchestrator

Modify `mypalclara/gateway/llm_orchestrator.py`:

At imports, add:
```python
from mypalclara.core.context_compactor import ContextCompactor
```

In `__init__`, add:
```python
self._compactor = ContextCompactor()
```

In `generate_with_tools()`, before the LLM call at line 161 (inside the loop), add compaction check:
```python
# Compact if context is growing too large during tool loop
budget = get_context_window("claude") if "claude" in str(tier) else get_context_window("default")
compaction = await self._compactor.compact_if_needed(working_messages, budget)
if compaction.was_compacted:
    working_messages = compaction.messages
    logger.info(f"Mid-loop compaction: saved {compaction.tokens_saved} tokens")
```

### Step 6: Run all tests, lint, commit

```bash
poetry run pytest tests/ -v --timeout=30
poetry run ruff check mypalclara/core/context_compactor.py tests/clara_core/test_context_compactor.py && poetry run ruff format mypalclara/core/context_compactor.py tests/clara_core/test_context_compactor.py
git add mypalclara/core/context_compactor.py tests/clara_core/test_context_compactor.py mypalclara/gateway/llm_orchestrator.py
git commit -m "feat: add progressive context compaction (OpenClaw-inspired)

Multi-stage summarization of old messages instead of dropping them.
Preserves system messages and recent 40% of history. Falls back
to drop-oldest if summarization fails. Integrated into LLM
orchestrator tool loop for mid-conversation compaction."
```

---

## Task 8: Subagent Orchestration (LLM-Driven)

**Files:**
- Create: `mypalclara/core/subagent/__init__.py`
- Create: `mypalclara/core/subagent/registry.py`
- Create: `mypalclara/core/subagent/tools.py`
- Create: `mypalclara/core/subagent/runner.py`
- Create: `tests/clara_core/test_subagent.py`
- Modify: `mypalclara/gateway/llm_orchestrator.py`

### Step 1: Write failing tests

Create `tests/clara_core/test_subagent.py`:

```python
"""Tests for subagent orchestration."""
import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus
from mypalclara.core.subagent.runner import SubagentRunner


class TestSubagentRegistry:
    def test_register_and_list(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Write a summary", tier="mid")
        assert record.status == SubagentStatus.RUNNING

        agents = registry.list_active()
        assert len(agents) == 1
        assert agents[0].task == "Write a summary"

    def test_kill_agent(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Task A")
        registry.kill(record.id)
        assert registry.get(record.id).status == SubagentStatus.KILLED

    def test_kill_all(self):
        registry = SubagentRegistry()
        registry.register("parent_1", "Task A")
        registry.register("parent_1", "Task B")
        killed = registry.kill_all("parent_1")
        assert killed == 2
        assert all(r.status == SubagentStatus.KILLED for r in registry.list_all())

    def test_complete_agent(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Task A")
        registry.complete(record.id, "Done successfully")
        assert registry.get(record.id).status == SubagentStatus.COMPLETED
        assert registry.get(record.id).result_summary == "Done successfully"

    def test_max_subagents_per_parent(self):
        registry = SubagentRegistry(max_per_parent=3)
        for i in range(3):
            registry.register("parent_1", f"Task {i}")
        with pytest.raises(RuntimeError, match="maximum"):
            registry.register("parent_1", "Task overflow")


class TestSubagentSteering:
    def test_steer_queues_instruction(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Task A")
        registry.steer(record.id, "Focus on error handling")
        instructions = registry.pop_steering(record.id)
        assert len(instructions) == 1
        assert instructions[0] == "Focus on error handling"

    def test_steer_rate_limited(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Task A")
        registry.steer(record.id, "Instruction 1")
        with pytest.raises(RuntimeError, match="rate"):
            registry.steer(record.id, "Instruction 2")

    def test_steer_dead_agent_raises(self):
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Task A")
        registry.kill(record.id)
        with pytest.raises(RuntimeError, match="not running"):
            registry.steer(record.id, "Too late")


class TestSubagentRunner:
    @pytest.mark.asyncio
    async def test_run_completes_task(self):
        registry = SubagentRegistry()
        mock_orchestrator_factory = MagicMock()

        async def mock_generate(*args, **kwargs):
            yield {"type": "complete", "content": "Task completed"}

        mock_orch = MagicMock()
        mock_orch.generate_with_tools = MagicMock(return_value=mock_generate())
        mock_orchestrator_factory.return_value = mock_orch

        runner = SubagentRunner(registry, mock_orchestrator_factory)
        record = registry.register("parent_1", "Summarize the document")

        result = await runner.run(record.id, tools=[], user_id="test_user")
        assert "completed" in result.lower() or registry.get(record.id).status == SubagentStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_timeout_kills_agent(self):
        registry = SubagentRegistry()

        async def slow_generate(*args, **kwargs):
            await asyncio.sleep(10)
            yield {"type": "complete", "content": "Never reached"}

        mock_orch = MagicMock()
        mock_orch.generate_with_tools = MagicMock(return_value=slow_generate())

        runner = SubagentRunner(registry, lambda: mock_orch, timeout_seconds=0.1)
        record = registry.register("parent_1", "Slow task")

        result = await runner.run(record.id, tools=[], user_id="test_user")
        assert registry.get(record.id).status in (SubagentStatus.FAILED, SubagentStatus.KILLED)
```

### Step 2: Run tests to verify they fail

Run: `poetry run pytest tests/clara_core/test_subagent.py -v`
Expected: ImportError

### Step 3: Implement SubagentRegistry

Create `mypalclara/core/subagent/__init__.py`:
```python
```

Create `mypalclara/core/subagent/registry.py`:

```python
"""Subagent lifecycle registry.

Manages registration, status tracking, steering, and cleanup
of sub-agents spawned by a parent agent.
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

STEER_MIN_INTERVAL_S = 2.0
DEFAULT_MAX_PER_PARENT = 5


class SubagentStatus(Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"


@dataclass
class SubagentRunRecord:
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
    def __init__(self, max_per_parent: int = DEFAULT_MAX_PER_PARENT):
        self._records: dict[str, SubagentRunRecord] = {}
        self._max_per_parent = max_per_parent

    def register(self, parent_id: str, task: str, tier: str = "mid", tool_subset: list[str] | None = None) -> SubagentRunRecord:
        active = [r for r in self._records.values() if r.parent_id == parent_id and r.status == SubagentStatus.RUNNING]
        if len(active) >= self._max_per_parent:
            raise RuntimeError(f"Maximum {self._max_per_parent} concurrent sub-agents per parent")

        agent_id = str(uuid.uuid4())[:8]
        session_key = f"agent:{parent_id}:sub:{agent_id}"
        record = SubagentRunRecord(
            id=agent_id,
            parent_id=parent_id,
            session_key=session_key,
            task=task,
            model_tier=tier,
            tool_subset=tool_subset,
        )
        self._records[agent_id] = record
        logger.info(f"Subagent registered: {agent_id} for parent {parent_id}: {task[:80]}")
        return record

    def get(self, agent_id: str) -> SubagentRunRecord | None:
        return self._records.get(agent_id)

    def list_active(self, parent_id: str | None = None) -> list[SubagentRunRecord]:
        records = self._records.values()
        if parent_id:
            records = [r for r in records if r.parent_id == parent_id]
        return [r for r in records if r.status == SubagentStatus.RUNNING]

    def list_all(self, parent_id: str | None = None) -> list[SubagentRunRecord]:
        if parent_id:
            return [r for r in self._records.values() if r.parent_id == parent_id]
        return list(self._records.values())

    def kill(self, agent_id: str) -> None:
        record = self._records.get(agent_id)
        if record:
            record.status = SubagentStatus.KILLED
            logger.info(f"Subagent killed: {agent_id}")

    def kill_all(self, parent_id: str) -> int:
        count = 0
        for record in self._records.values():
            if record.parent_id == parent_id and record.status == SubagentStatus.RUNNING:
                record.status = SubagentStatus.KILLED
                count += 1
        logger.info(f"Killed {count} sub-agents for parent {parent_id}")
        return count

    def complete(self, agent_id: str, result_summary: str) -> None:
        record = self._records.get(agent_id)
        if record:
            record.status = SubagentStatus.COMPLETED
            record.result_summary = result_summary
            logger.info(f"Subagent completed: {agent_id}")

    def fail(self, agent_id: str, error: str) -> None:
        record = self._records.get(agent_id)
        if record:
            record.status = SubagentStatus.FAILED
            record.result_summary = f"Error: {error}"
            logger.warning(f"Subagent failed: {agent_id}: {error}")

    def steer(self, agent_id: str, instruction: str) -> None:
        record = self._records.get(agent_id)
        if not record:
            raise RuntimeError(f"Subagent {agent_id} not found")
        if record.status != SubagentStatus.RUNNING:
            raise RuntimeError(f"Subagent {agent_id} not running (status: {record.status.value})")

        now = time.monotonic()
        if now - record._last_steer_time < STEER_MIN_INTERVAL_S:
            raise RuntimeError(f"Steering rate limited (min {STEER_MIN_INTERVAL_S}s between steers)")

        record._steering_queue.append(instruction)
        record._last_steer_time = now
        logger.info(f"Steering subagent {agent_id}: {instruction[:80]}")

    def pop_steering(self, agent_id: str) -> list[str]:
        record = self._records.get(agent_id)
        if not record:
            return []
        instructions = list(record._steering_queue)
        record._steering_queue.clear()
        return instructions
```

### Step 4: Implement SubagentRunner

Create `mypalclara/core/subagent/runner.py`:

```python
"""Subagent execution engine.

Runs sub-agents as asyncio tasks with their own LLMOrchestrator
instances, timeout management, and steering injection.
"""

import asyncio
import logging

from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus
from mypalclara.core.llm.messages import SystemMessage, UserMessage

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 600  # 10 minutes


class SubagentRunner:
    def __init__(self, registry: SubagentRegistry, orchestrator_factory, timeout_seconds: float = DEFAULT_TIMEOUT_S):
        self._registry = registry
        self._orchestrator_factory = orchestrator_factory
        self._timeout = timeout_seconds

    async def run(self, agent_id: str, tools: list, user_id: str) -> str:
        record = self._registry.get(agent_id)
        if not record:
            return f"Error: subagent {agent_id} not found"

        orchestrator = self._orchestrator_factory()

        # Build minimal prompt
        messages = [
            SystemMessage(content=f"You are a sub-agent. Your task: {record.task}\n\nComplete this task and report your results concisely."),
            UserMessage(content=record.task),
        ]

        try:
            result = await asyncio.wait_for(
                self._execute(orchestrator, messages, tools, user_id, agent_id, record),
                timeout=self._timeout,
            )
            self._registry.complete(agent_id, result)
            return result
        except asyncio.TimeoutError:
            self._registry.fail(agent_id, f"Timed out after {self._timeout}s")
            return f"Sub-agent timed out after {self._timeout}s"
        except Exception as e:
            self._registry.fail(agent_id, str(e))
            return f"Sub-agent error: {e}"

    async def _execute(self, orchestrator, messages, tools, user_id, agent_id, record) -> str:
        result_content = ""

        async for event in orchestrator.generate_with_tools(
            messages=messages,
            tools=tools,
            user_id=user_id,
            request_id=record.session_key,
            tier=record.model_tier,
        ):
            # Check for kill signal
            current = self._registry.get(agent_id)
            if current and current.status == SubagentStatus.KILLED:
                return "Sub-agent was killed"

            # Inject steering instructions
            steering = self._registry.pop_steering(agent_id)
            for instruction in steering:
                messages.append(SystemMessage(content=f"[STEERING] {instruction}"))

            if event.get("type") == "complete":
                result_content = event.get("content", "")

        return result_content or "Sub-agent completed with no output"
```

### Step 5: Implement subagent tools

Create `mypalclara/core/subagent/tools.py`:

```python
"""Subagent tools exposed to the LLM.

Four tools: spawn, list, kill, steer.
"""

import json
import logging
from mypalclara.core.subagent.registry import SubagentRegistry, SubagentRunRecord
from mypalclara.core.subagent.runner import SubagentRunner

logger = logging.getLogger(__name__)


def make_subagent_tools(registry: SubagentRegistry, runner: SubagentRunner) -> list[dict]:
    """Create tool definitions for subagent orchestration."""
    return [
        {
            "name": "subagent_spawn",
            "description": "Create a sub-agent to work on a task in parallel. The sub-agent gets its own conversation and tool access. Use for complex tasks that can be decomposed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "description": "Clear description of what the sub-agent should accomplish"},
                    "model_tier": {"type": "string", "enum": ["high", "mid", "low"], "description": "Model tier (default: mid)"},
                    "tools": {"type": "array", "items": {"type": "string"}, "description": "Optional: limit which tools the sub-agent can use"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "subagent_list",
            "description": "List active and recent sub-agents with their status, runtime, and token usage.",
            "parameters": {"type": "object", "properties": {}},
        },
        {
            "name": "subagent_kill",
            "description": "Terminate a sub-agent by ID, or pass 'all' to kill all sub-agents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Sub-agent ID or 'all'"},
                },
                "required": ["id"],
            },
        },
        {
            "name": "subagent_steer",
            "description": "Send a corrective instruction to a running sub-agent. Rate limited to one steer per 2 seconds.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Sub-agent ID"},
                    "instruction": {"type": "string", "description": "Corrective instruction or guidance"},
                },
                "required": ["id", "instruction"],
            },
        },
    ]


async def handle_subagent_tool(
    tool_name: str,
    arguments: dict,
    parent_id: str,
    registry: SubagentRegistry,
    runner: SubagentRunner,
    available_tools: list,
    user_id: str,
) -> str:
    """Execute a subagent tool call."""
    if tool_name == "subagent_spawn":
        task = arguments["task"]
        tier = arguments.get("model_tier", "mid")
        tool_subset = arguments.get("tools")

        try:
            record = registry.register(parent_id, task, tier=tier, tool_subset=tool_subset)
        except RuntimeError as e:
            return str(e)

        # Filter tools if subset specified
        tools = available_tools
        if tool_subset:
            tools = [t for t in available_tools if t.get("name") in tool_subset]

        # Run asynchronously
        import asyncio
        asyncio.create_task(runner.run(record.id, tools, user_id))

        return f"Sub-agent spawned: id={record.id}, task='{task[:80]}', tier={tier}"

    elif tool_name == "subagent_list":
        agents = registry.list_all(parent_id)
        if not agents:
            return "No sub-agents."
        lines = []
        for a in agents:
            elapsed = f"{a.token_usage} tokens" if a.token_usage else "running"
            lines.append(f"- {a.id}: [{a.status.value}] {a.task[:60]} ({elapsed})")
        return "\n".join(lines)

    elif tool_name == "subagent_kill":
        agent_id = arguments["id"]
        if agent_id == "all":
            count = registry.kill_all(parent_id)
            return f"Killed {count} sub-agents."
        registry.kill(agent_id)
        return f"Killed sub-agent {agent_id}."

    elif tool_name == "subagent_steer":
        try:
            registry.steer(arguments["id"], arguments["instruction"])
            return f"Steering instruction sent to {arguments['id']}."
        except RuntimeError as e:
            return str(e)

    return f"Unknown subagent tool: {tool_name}"
```

### Step 6: Run tests to verify they pass

Run: `poetry run pytest tests/clara_core/test_subagent.py -v`
Expected: All 9 tests PASS

### Step 7: Integrate into gateway

Modify `mypalclara/gateway/llm_orchestrator.py` to:
- Import subagent registry and tools
- In tool execution routing, check for `subagent_*` tool names and route to `handle_subagent_tool()`
- Initialize `SubagentRegistry` and `SubagentRunner` in `LLMOrchestrator.__init__()`

### Step 8: Run all tests, lint, commit

```bash
poetry run pytest tests/ -v --timeout=30
poetry run ruff check mypalclara/core/subagent/ tests/clara_core/test_subagent.py && poetry run ruff format mypalclara/core/subagent/ tests/clara_core/test_subagent.py
git add mypalclara/core/subagent/ tests/clara_core/test_subagent.py mypalclara/gateway/llm_orchestrator.py
git commit -m "feat: add LLM-driven subagent orchestration (OpenClaw-inspired)

Four tools exposed to the LLM: subagent_spawn, subagent_list,
subagent_kill, subagent_steer. Sub-agents get own LLMOrchestrator
with minimal prompt mode. Rate-limited steering, 10-min timeout,
max 5 concurrent per parent. 1-level depth limit."
```

---

## Task 9: Final Integration Test

**Files:**
- Create: `tests/integration/test_openclaw_features.py`

### Step 1: Write integration test

```python
"""Integration test: all OpenClaw-inspired features working together."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from mypalclara.core.tool_guard import ToolLoopGuard, LoopAction
from mypalclara.core.tool_result_guard import ToolResultGuard
from mypalclara.core.llm.failover import ResilientProvider, CooldownManager, classify_error, FailoverReason
from mypalclara.core.prompt_builder import PromptBuilder, PromptMode
from mypalclara.core.workspace_loader import WorkspaceLoader
from mypalclara.core.tool_summaries import build_tool_summary_section
from mypalclara.core.context_compactor import ContextCompactor
from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus


class TestFeatureIntegration:
    """Verify all features can be instantiated and work together."""

    def test_all_modules_import(self):
        """Smoke test: all new modules import without error."""
        assert ToolLoopGuard is not None
        assert ToolResultGuard is not None
        assert ResilientProvider is not None
        assert PromptMode is not None
        assert WorkspaceLoader is not None
        assert build_tool_summary_section is not None
        assert ContextCompactor is not None
        assert SubagentRegistry is not None

    def test_loop_guard_with_result_guard(self):
        """Loop guard + result guard working in sequence."""
        loop_guard = ToolLoopGuard()
        result_guard = ToolResultGuard(max_chars=100)

        # Simulate a tool call
        check = loop_guard.check("search", {"q": "test"})
        assert check.action == LoopAction.ALLOW

        # Cap the result
        capped = result_guard.cap("search", "call_1", "x" * 500)
        assert capped.was_truncated

        # Record capped result
        loop_guard.record_result("search", {"q": "test"}, capped.content)

    def test_prompt_builder_with_workspace(self, tmp_path):
        """Prompt builder loads workspace files."""
        (tmp_path / "SOUL.md").write_text("Be helpful and warm.")
        (tmp_path / "IDENTITY.md").write_text("- **Name:** TestBot\n- **Vibe:** friendly")

        loader = WorkspaceLoader()
        files = loader.load(tmp_path)
        assert len(files) == 2

        # Tool summaries can be generated
        tools = [{"name": "test_tool", "description": "A test tool", "parameters": {}}]
        lines = build_tool_summary_section(tools)
        assert len(lines) > 0

    def test_subagent_lifecycle(self):
        """Full subagent lifecycle: register, steer, complete."""
        registry = SubagentRegistry()
        record = registry.register("parent_1", "Analyze code")
        assert record.status == SubagentStatus.RUNNING

        # Steer
        registry.steer(record.id, "Focus on error handling")
        instructions = registry.pop_steering(record.id)
        assert len(instructions) == 1

        # Complete
        registry.complete(record.id, "Found 3 issues")
        assert registry.get(record.id).status == SubagentStatus.COMPLETED

    def test_failure_classification(self):
        """Error classification covers all types."""
        assert classify_error(Exception("HTTP 401")) == FailoverReason.AUTH
        assert classify_error(Exception("HTTP 429")) == FailoverReason.RATE_LIMIT
        assert classify_error(Exception("context length")) == FailoverReason.CONTEXT_OVERFLOW
        assert classify_error(TimeoutError()) == FailoverReason.TRANSIENT
        assert classify_error(Exception("wat")) == FailoverReason.UNKNOWN
```

### Step 2: Run full test suite

Run: `poetry run pytest tests/ -v --timeout=60`
Expected: ALL tests PASS

### Step 3: Lint everything and final commit

```bash
poetry run ruff check . && poetry run ruff format .
git add tests/integration/test_openclaw_features.py
git commit -m "test: add integration tests for all OpenClaw-inspired features

Smoke tests verifying all 8 features can be instantiated and
work together: loop guard + result guard, prompt builder +
workspace files + tool summaries, subagent lifecycle, and
failure classification."
```

---

## Summary

| Task | Feature | New Files | Test Count |
|------|---------|-----------|------------|
| 1 | Tool Loop Detection | `core/tool_guard.py` | 12 |
| 2 | Tool Result Capping | `core/tool_result_guard.py` | 8 |
| 3 | Provider Failover | `core/llm/failover.py` | 13 |
| 4 | Compositional Prompt Builder | (refactor `prompt_builder.py`) | 5 |
| 5 | Workspace Files | `core/workspace_loader.py`, `workspace/*.md` | 7 |
| 6 | Tool Summaries | `core/tool_summaries.py` | 5 |
| 7 | Context Compaction | `core/context_compactor.py` | 5 |
| 8 | Subagent Orchestration | `core/subagent/` (3 files) | 9 |
| 9 | Integration | `tests/integration/` | 5 |
| **Total** | | **13 new files** | **~69 tests** |
