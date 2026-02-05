"""MCP metrics tracking and rate limiting.

Provides:
- Tool call tracking with timing and success/failure
- Aggregated usage metrics per user per day
- Rate limiting with configurable thresholds
- Memory integration for usage patterns
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MCPMetricsTracker:
    """Tracks MCP tool call metrics and enforces rate limits.

    Usage:
        tracker = MCPMetricsTracker()

        # Before tool call
        if not await tracker.check_rate_limit(user_id, server_name, tool_name):
            raise RateLimitExceeded(...)

        # Track the call
        call_id = await tracker.start_call(user_id, server_name, tool_name, args)

        try:
            result = await execute_tool(...)
            await tracker.complete_call(call_id, success=True, result_preview=result[:500])
        except Exception as e:
            await tracker.complete_call(call_id, success=False, error=str(e))
    """

    def __init__(self) -> None:
        """Initialize the metrics tracker."""
        self._lock = asyncio.Lock()
        self._pending_calls: dict[str, dict[str, Any]] = {}

    async def start_call(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """Record the start of a tool call.

        Args:
            user_id: User making the call
            server_name: MCP server name
            tool_name: Tool being called
            arguments: Tool arguments (will be truncated)
            session_id: Optional session ID
            request_id: Optional gateway request ID

        Returns:
            Call ID for tracking completion
        """
        try:
            from db import SessionLocal
            from db.mcp_models import MCPServer, MCPToolCall

            db = SessionLocal()
            try:
                # Truncate arguments for storage
                args_json = None
                if arguments:
                    args_str = json.dumps(arguments)
                    args_json = args_str[:2000] if len(args_str) > 2000 else args_str

                # Get server ID if exists
                server = (
                    db.query(MCPServer)
                    .filter(MCPServer.name == server_name)
                    .filter((MCPServer.user_id == user_id) | (MCPServer.user_id.is_(None)))
                    .first()
                )

                call = MCPToolCall(
                    user_id=user_id,
                    session_id=session_id,
                    request_id=request_id,
                    server_id=server.id if server else None,
                    server_name=server_name,
                    tool_name=tool_name,
                    arguments=args_json,
                    started_at=utcnow(),
                )
                db.add(call)
                db.commit()

                # Track in memory for completion
                self._pending_calls[call.id] = {
                    "started_at": call.started_at,
                    "user_id": user_id,
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "arguments": arguments,
                }

                return call.id

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[MCPMetrics] Failed to start call tracking: {e}")
            # Return a temporary ID so completion can still be attempted
            import uuid

            temp_id = f"temp-{uuid.uuid4()}"
            self._pending_calls[temp_id] = {
                "started_at": utcnow(),
                "user_id": user_id,
                "server_name": server_name,
                "tool_name": tool_name,
                "arguments": arguments,
            }
            return temp_id

    async def complete_call(
        self,
        call_id: str,
        success: bool = True,
        result_preview: str | None = None,
        error_message: str | None = None,
        error_type: str | None = None,
        store_to_memory: bool = False,
        task_description: str | None = None,
    ) -> None:
        """Record the completion of a tool call.

        Args:
            call_id: Call ID from start_call
            success: Whether the call succeeded
            result_preview: First N chars of result
            error_message: Error message if failed
            error_type: Error type (timeout, connection, execution)
            store_to_memory: If True and successful, store to mem0 for learning
            task_description: Optional description of what the user was trying to do
        """
        completed_at = utcnow()

        # Get pending call info
        pending = self._pending_calls.pop(call_id, None)
        if not pending:
            logger.debug(f"[MCPMetrics] No pending call found for {call_id}")
            return

        # Calculate duration
        started_at = pending.get("started_at")
        duration_ms = None
        if started_at:
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

        # Skip DB update for temp IDs
        if call_id.startswith("temp-"):
            await self._update_aggregates(
                pending["user_id"],
                pending["server_name"],
                pending["tool_name"],
                success,
                duration_ms,
            )
            return

        try:
            from db import SessionLocal
            from db.mcp_models import MCPServer, MCPToolCall

            db = SessionLocal()
            try:
                call = db.query(MCPToolCall).filter(MCPToolCall.id == call_id).first()
                if call:
                    call.completed_at = completed_at
                    call.duration_ms = duration_ms
                    call.success = success
                    call.result_preview = result_preview[:1000] if result_preview else None
                    call.error_message = error_message
                    call.error_type = error_type
                    db.commit()

                    # Update server usage stats
                    if call.server_id:
                        server = db.query(MCPServer).filter(MCPServer.id == call.server_id).first()
                        if server:
                            server.total_tool_calls = (server.total_tool_calls or 0) + 1
                            server.last_used_at = completed_at
                            db.commit()

            finally:
                db.close()

            # Update aggregates
            await self._update_aggregates(
                pending["user_id"],
                pending["server_name"],
                pending["tool_name"],
                success,
                duration_ms,
            )

            # Store to mem0 for learning if requested and successful
            if store_to_memory and success:
                await self._store_to_memory(
                    pending["user_id"],
                    pending["server_name"],
                    pending["tool_name"],
                    task_description,
                    result_preview,
                    pending.get("arguments"),
                )

        except Exception as e:
            logger.warning(f"[MCPMetrics] Failed to complete call tracking: {e}")

    async def _update_aggregates(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
        success: bool,
        duration_ms: int | None,
    ) -> None:
        """Update daily aggregate metrics."""
        try:
            from db import SessionLocal
            from db.mcp_models import MCPUsageMetrics

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            db = SessionLocal()
            try:
                # Find or create metrics record
                metrics = (
                    db.query(MCPUsageMetrics)
                    .filter(
                        MCPUsageMetrics.user_id == user_id,
                        MCPUsageMetrics.server_name == server_name,
                        MCPUsageMetrics.date == date_str,
                    )
                    .first()
                )

                now = utcnow()

                if not metrics:
                    metrics = MCPUsageMetrics(
                        user_id=user_id,
                        server_name=server_name,
                        date=date_str,
                        call_count=0,
                        success_count=0,
                        error_count=0,
                        timeout_count=0,
                        total_duration_ms=0,
                        first_call_at=now,
                        tool_counts="{}",
                    )
                    db.add(metrics)

                # Update counts
                metrics.call_count += 1
                if success:
                    metrics.success_count += 1
                else:
                    metrics.error_count += 1

                if duration_ms:
                    metrics.total_duration_ms += duration_ms
                    metrics.avg_duration_ms = metrics.total_duration_ms / metrics.call_count

                metrics.last_call_at = now

                # Update tool breakdown
                tool_counts = json.loads(metrics.tool_counts or "{}")
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
                metrics.tool_counts = json.dumps(tool_counts)

                db.commit()

            finally:
                db.close()

        except Exception as e:
            logger.debug(f"[MCPMetrics] Failed to update aggregates: {e}")

    async def _store_to_memory(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
        task_description: str | None,
        result_preview: str | None,
        arguments: dict[str, Any] | None,
    ) -> None:
        """Store successful tool call to mem0 for learning.

        This is called after successful tool calls when store_to_memory=True.
        It uses the MCP memory integration to store patterns that can be
        retrieved later for tool suggestions.
        """
        try:
            from clara_core.mcp.memory_integration import on_tool_success

            await on_tool_success(
                user_id=user_id,
                server_name=server_name,
                tool_name=tool_name,
                task_description=task_description,
                result_summary=result_preview,
                arguments=arguments,
            )
        except ImportError:
            logger.debug("[MCPMetrics] Memory integration not available")
        except Exception as e:
            logger.debug(f"[MCPMetrics] Failed to store to memory: {e}")

    async def check_rate_limit(
        self,
        user_id: str,
        server_name: str,
        tool_name: str,
    ) -> bool:
        """Check if a tool call is allowed under rate limits.

        Args:
            user_id: User making the call
            server_name: MCP server name
            tool_name: Tool being called

        Returns:
            True if allowed, False if rate limited
        """
        try:
            from db import SessionLocal
            from db.mcp_models import MCPRateLimit

            db = SessionLocal()
            try:
                now = utcnow()

                # Find applicable rate limits (most specific first)
                limits = (
                    db.query(MCPRateLimit)
                    .filter(MCPRateLimit.enabled == True)
                    .filter((MCPRateLimit.user_id == user_id) | (MCPRateLimit.user_id.is_(None)))
                    .filter((MCPRateLimit.server_name == server_name) | (MCPRateLimit.server_name.is_(None)))
                    .filter((MCPRateLimit.tool_name == tool_name) | (MCPRateLimit.tool_name.is_(None)))
                    .all()
                )

                for limit in limits:
                    # Check minute limit
                    if limit.max_calls_per_minute:
                        if limit.minute_window_start and (now - limit.minute_window_start).total_seconds() < 60:
                            if limit.current_minute_count >= limit.max_calls_per_minute:
                                return False
                        else:
                            # Reset window
                            limit.minute_window_start = now
                            limit.current_minute_count = 0

                    # Check hour limit
                    if limit.max_calls_per_hour:
                        if limit.hour_window_start and (now - limit.hour_window_start).total_seconds() < 3600:
                            if limit.current_hour_count >= limit.max_calls_per_hour:
                                return False
                        else:
                            limit.hour_window_start = now
                            limit.current_hour_count = 0

                    # Check day limit
                    if limit.max_calls_per_day:
                        if limit.day_window_start and (now - limit.day_window_start).total_seconds() < 86400:
                            if limit.current_day_count >= limit.max_calls_per_day:
                                return False
                        else:
                            limit.day_window_start = now
                            limit.current_day_count = 0

                    # Increment counters
                    limit.current_minute_count = (limit.current_minute_count or 0) + 1
                    limit.current_hour_count = (limit.current_hour_count or 0) + 1
                    limit.current_day_count = (limit.current_day_count or 0) + 1

                db.commit()
                return True

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[MCPMetrics] Rate limit check failed: {e}")
            # Fail open on errors
            return True

    async def get_user_stats(
        self,
        user_id: str,
        days: int = 7,
    ) -> dict[str, Any]:
        """Get usage statistics for a user.

        Args:
            user_id: User ID
            days: Number of days to include

        Returns:
            Statistics dict
        """
        try:
            from datetime import timedelta

            from db import SessionLocal
            from db.mcp_models import MCPUsageMetrics

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

            db = SessionLocal()
            try:
                metrics = (
                    db.query(MCPUsageMetrics)
                    .filter(MCPUsageMetrics.user_id == user_id)
                    .filter(MCPUsageMetrics.date >= cutoff)
                    .all()
                )

                if not metrics:
                    return {"total_calls": 0, "servers": {}, "days": days}

                # Aggregate
                total_calls = sum(m.call_count for m in metrics)
                total_success = sum(m.success_count for m in metrics)
                total_errors = sum(m.error_count for m in metrics)
                total_duration = sum(m.total_duration_ms or 0 for m in metrics)

                # By server
                by_server: dict[str, int] = {}
                for m in metrics:
                    by_server[m.server_name] = by_server.get(m.server_name, 0) + m.call_count

                return {
                    "total_calls": total_calls,
                    "success_rate": (total_success / total_calls * 100) if total_calls else 0,
                    "error_count": total_errors,
                    "avg_duration_ms": (total_duration / total_calls) if total_calls else 0,
                    "servers": by_server,
                    "days": days,
                }

            finally:
                db.close()

        except Exception as e:
            logger.warning(f"[MCPMetrics] Failed to get user stats: {e}")
            return {"error": str(e)}


# Global singleton
_tracker: MCPMetricsTracker | None = None


def get_metrics_tracker() -> MCPMetricsTracker:
    """Get the global metrics tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = MCPMetricsTracker()
    return _tracker


async def track_tool_call(
    user_id: str,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
):
    """Context manager for tracking tool calls.

    Usage:
        async with track_tool_call(user_id, server, tool, args) as tracker:
            result = await call_tool(...)
            tracker.set_result(result)
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _track():
        tracker = get_metrics_tracker()
        call_id = await tracker.start_call(user_id, server_name, tool_name, arguments, session_id, request_id)

        class CallTracker:
            def __init__(self):
                self.result_preview = None
                self.success = True
                self.error_message = None
                self.error_type = None

            def set_result(self, result: str | None):
                self.result_preview = result[:500] if result else None

            def set_error(self, message: str, error_type: str = "execution"):
                self.success = False
                self.error_message = message
                self.error_type = error_type

        call_tracker = CallTracker()

        try:
            yield call_tracker
        except asyncio.TimeoutError:
            call_tracker.set_error("Tool call timed out", "timeout")
            raise
        except ConnectionError as e:
            call_tracker.set_error(str(e), "connection")
            raise
        except Exception as e:
            call_tracker.set_error(str(e), "execution")
            raise
        finally:
            await tracker.complete_call(
                call_id,
                success=call_tracker.success,
                result_preview=call_tracker.result_preview,
                error_message=call_tracker.error_message,
                error_type=call_tracker.error_type,
            )

    return _track()
