"""Tool audit logging for Clara.

Provides audit trail functionality for tool executions,
storing records in the database for compliance and debugging.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    """Audit entry for a tool execution.

    Attributes:
        user_id: User who executed the tool
        tool_name: Name of the tool executed
        platform: Platform where execution occurred
        parameters: Tool parameters (may be sanitized)
        result_status: Execution result ("success", "error", "denied")
        risk_level: Tool's risk level
        intent: Tool's intent
        error_message: Error message if applicable
        execution_time_ms: Execution time in milliseconds
    """

    user_id: str
    tool_name: str
    platform: str
    parameters: dict[str, Any]
    result_status: str
    risk_level: str
    intent: str
    error_message: str | None = None
    execution_time_ms: int | None = None
    channel_id: str | None = None


class AuditLogger:
    """Logs tool executions to database and console.

    Usage:
        audit_logger = AuditLogger(session_factory)
        await audit_logger.log(entry)
        history = await audit_logger.get_user_history(user_id)
    """

    def __init__(self, session_factory: callable | None = None) -> None:
        """Initialize the audit logger.

        Args:
            session_factory: SQLAlchemy session factory for database logging
        """
        self.session_factory = session_factory
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable audit logging."""
        self._enabled = enabled

    async def log(self, entry: AuditEntry) -> None:
        """Log a tool execution to database and console.

        Args:
            entry: Audit entry to log
        """
        if not self._enabled:
            return

        # Console log with emoji
        status_emoji = {
            "success": "✅",
            "error": "❌",
            "denied": "🚫",
            "allowed": "✨",
        }.get(entry.result_status, "❓")

        logger.info(
            f"{status_emoji} Tool: {entry.tool_name} | User: {entry.user_id} | "
            f"Risk: {entry.risk_level} | Status: {entry.result_status}"
            + (f" | Error: {entry.error_message}" if entry.error_message else "")
        )

        # Database log
        if self.session_factory:
            try:
                await self._log_to_db(entry)
            except Exception as e:
                logger.error(f"Failed to log audit entry to database: {e}")

    async def _log_to_db(self, entry: AuditEntry) -> None:
        """Log entry to database."""
        from db.models import ToolAuditLog

        session = self.session_factory()
        try:
            log_entry = ToolAuditLog(
                user_id=entry.user_id,
                tool_name=entry.tool_name,
                platform=entry.platform,
                parameters=entry.parameters,
                result_status=entry.result_status,
                error_message=entry.error_message,
                execution_time_ms=entry.execution_time_ms,
                risk_level=entry.risk_level,
                intent=entry.intent,
                channel_id=entry.channel_id,
            )
            session.add(log_entry)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def get_user_history(
        self,
        user_id: str,
        limit: int = 100,
        tool_name: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get tool execution history for a user.

        Args:
            user_id: User ID to get history for
            limit: Maximum entries to return
            tool_name: Optional filter by tool name
            status: Optional filter by status

        Returns:
            List of audit log entries as dicts
        """
        if not self.session_factory:
            return []

        from sqlalchemy import select

        from db.models import ToolAuditLog

        session = self.session_factory()
        try:
            query = (
                select(ToolAuditLog)
                .where(ToolAuditLog.user_id == user_id)
                .order_by(ToolAuditLog.timestamp.desc())
                .limit(limit)
            )

            if tool_name:
                query = query.where(ToolAuditLog.tool_name == tool_name)
            if status:
                query = query.where(ToolAuditLog.result_status == status)

            result = session.execute(query)
            logs = result.scalars().all()

            return [
                {
                    "id": log.id,
                    "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                    "tool_name": log.tool_name,
                    "platform": log.platform,
                    "result_status": log.result_status,
                    "error_message": log.error_message,
                    "execution_time_ms": log.execution_time_ms,
                    "risk_level": log.risk_level,
                    "intent": log.intent,
                }
                for log in logs
            ]
        finally:
            session.close()

    async def get_tool_stats(
        self,
        tool_name: str | None = None,
        hours: int = 24,
    ) -> dict[str, Any]:
        """Get aggregated statistics for tool usage.

        Args:
            tool_name: Optional filter by tool name
            hours: Hours to look back

        Returns:
            Stats dict with counts, avg execution time, error rate
        """
        if not self.session_factory:
            return {}

        from datetime import timedelta

        from sqlalchemy import func, select

        from db.models import ToolAuditLog

        session = self.session_factory()
        try:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)

            base_query = select(ToolAuditLog).where(ToolAuditLog.timestamp >= since)
            if tool_name:
                base_query = base_query.where(ToolAuditLog.tool_name == tool_name)

            result = session.execute(base_query)
            logs = result.scalars().all()

            if not logs:
                return {
                    "total_calls": 0,
                    "success_count": 0,
                    "error_count": 0,
                    "denied_count": 0,
                    "avg_execution_time_ms": 0,
                    "error_rate": 0,
                }

            total = len(logs)
            success = sum(1 for l in logs if l.result_status == "success")
            errors = sum(1 for l in logs if l.result_status == "error")
            denied = sum(1 for l in logs if l.result_status == "denied")

            exec_times = [l.execution_time_ms for l in logs if l.execution_time_ms]
            avg_time = sum(exec_times) / len(exec_times) if exec_times else 0

            return {
                "total_calls": total,
                "success_count": success,
                "error_count": errors,
                "denied_count": denied,
                "avg_execution_time_ms": round(avg_time, 2),
                "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
            }
        finally:
            session.close()


# Global audit logger singleton
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Get the global audit logger singleton.

    Returns:
        AuditLogger instance
    """
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger


def init_audit_logger(session_factory: callable) -> AuditLogger:
    """Initialize the audit logger with a database session factory.

    Args:
        session_factory: SQLAlchemy session factory

    Returns:
        Initialized AuditLogger
    """
    global _audit_logger
    _audit_logger = AuditLogger(session_factory)
    return _audit_logger


def reset_audit_logger() -> None:
    """Reset the global audit logger. Useful for testing."""
    global _audit_logger
    _audit_logger = None
