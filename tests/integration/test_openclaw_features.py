"""Integration test: all OpenClaw-inspired features working together."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mypalclara.core.context_compactor import ContextCompactor
from mypalclara.core.llm.failover import (
    CooldownManager,
    FailoverReason,
    ResilientProvider,
    classify_error,
)
from mypalclara.core.prompt_builder import PromptBuilder, PromptMode
from mypalclara.core.subagent.registry import SubagentRegistry, SubagentStatus
from mypalclara.core.tool_guard import LoopAction, ToolLoopGuard
from mypalclara.core.tool_result_guard import ToolResultGuard
from mypalclara.core.tool_summaries import build_tool_summary_section
from mypalclara.core.workspace_loader import WorkspaceLoader


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
