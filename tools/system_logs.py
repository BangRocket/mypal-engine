"""System logs tools.

Provides tools for searching and retrieving system logs from PostgreSQL.
Tools: search_logs, get_recent_logs, get_error_logs

Useful for debugging issues and monitoring system health.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ._base import ToolContext, ToolDef

MODULE_NAME = "system_logs"
MODULE_VERSION = "1.0.0"

SYSTEM_PROMPT = """
## System Logs Access
You can search and retrieve system logs to help debug issues.

**Tools:**
- `search_logs` - Search logs by keyword, logger name, or level
- `get_recent_logs` - Get the most recent log entries
- `get_error_logs` - Get recent errors and exceptions

**When to Use:**
- Debugging file storage or S3 issues
- Investigating why something failed
- Checking system health or startup messages
- User reports a bug or error

**Note:** Logs are stored in PostgreSQL and include full tracebacks for errors.
""".strip()


# Database session factory (set during initialization)
_session_factory = None


def _get_session():
    """Get a database session."""
    if _session_factory is None:
        raise RuntimeError("System logs not initialized - no database connection")
    return _session_factory()


# --- Tool Handlers ---


async def search_logs(args: dict[str, Any], ctx: ToolContext) -> str:
    """Search system logs by keyword, logger, or level."""
    from db.models import LogEntry

    query = args.get("query", "")
    logger_name = args.get("logger_name", "")
    level = args.get("level", "").upper()
    limit = min(args.get("limit", 50), 200)
    hours = args.get("hours", 24)

    try:
        session = _get_session()
        try:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

            q = session.query(LogEntry).filter(LogEntry.timestamp >= since)

            if query:
                q = q.filter(LogEntry.message.ilike(f"%{query}%"))

            if logger_name:
                q = q.filter(LogEntry.logger_name.ilike(f"%{logger_name}%"))

            if level:
                q = q.filter(LogEntry.level == level)

            logs = q.order_by(LogEntry.timestamp.desc()).limit(limit).all()

            if not logs:
                filters = []
                if query:
                    filters.append(f"query='{query}'")
                if logger_name:
                    filters.append(f"logger='{logger_name}'")
                if level:
                    filters.append(f"level={level}")
                filter_str = ", ".join(filters) if filters else "none"
                return f"No logs found in the last {hours}h with filters: {filter_str}"

            result = f"Found {len(logs)} log entries:\n\n"
            for log in logs:
                ts = log.timestamp.strftime("%m-%d %H:%M:%S")
                msg = log.message[:200] + ("..." if len(log.message) > 200 else "")
                result += f"[{ts}] **{log.level}** `{log.logger_name}`: {msg}\n"
                if log.exception:
                    # Show first few lines of traceback
                    exc_lines = log.exception.strip().split("\n")[-3:]
                    result += "```\n" + "\n".join(exc_lines) + "\n```\n"

            return result

        finally:
            session.close()

    except Exception as e:
        return f"Error searching logs: {e}"


async def get_recent_logs(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get the most recent log entries."""
    from db.models import LogEntry

    limit = min(args.get("limit", 30), 100)
    logger_name = args.get("logger_name", "")

    try:
        session = _get_session()
        try:
            q = session.query(LogEntry)

            if logger_name:
                q = q.filter(LogEntry.logger_name.ilike(f"%{logger_name}%"))

            logs = q.order_by(LogEntry.timestamp.desc()).limit(limit).all()

            if not logs:
                return "No log entries found."

            result = f"Last {len(logs)} log entries:\n\n"
            for log in logs:
                ts = log.timestamp.strftime("%m-%d %H:%M:%S")
                msg = log.message[:150] + ("..." if len(log.message) > 150 else "")
                result += f"[{ts}] {log.level:8} `{log.logger_name}`: {msg}\n"

            return result

        finally:
            session.close()

    except Exception as e:
        return f"Error getting logs: {e}"


async def get_error_logs(args: dict[str, Any], ctx: ToolContext) -> str:
    """Get recent error and exception logs."""
    from db.models import LogEntry

    limit = min(args.get("limit", 20), 50)
    hours = args.get("hours", 24)
    include_warnings = args.get("include_warnings", False)

    try:
        session = _get_session()
        try:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=hours)

            levels = ["ERROR", "CRITICAL"]
            if include_warnings:
                levels.append("WARNING")

            logs = (
                session.query(LogEntry)
                .filter(LogEntry.timestamp >= since)
                .filter(LogEntry.level.in_(levels))
                .order_by(LogEntry.timestamp.desc())
                .limit(limit)
                .all()
            )

            if not logs:
                return f"No errors found in the last {hours} hours."

            result = f"Found {len(logs)} error(s) in the last {hours}h:\n\n"
            for log in logs:
                ts = log.timestamp.strftime("%m-%d %H:%M:%S")
                result += f"### [{ts}] {log.level} - `{log.logger_name}`\n"
                result += f"{log.message}\n"
                if log.exception:
                    result += f"```\n{log.exception[:500]}\n```\n"
                result += "\n"

            return result

        finally:
            session.close()

    except Exception as e:
        return f"Error getting error logs: {e}"


# --- Tool Definitions ---

TOOLS = [
    ToolDef(
        name="search_logs",
        description=(
            "Search system logs by keyword, logger name, or level. "
            "Use this to investigate issues, find errors, or check what happened. "
            "Logs include timestamps, levels (DEBUG/INFO/WARNING/ERROR), and full tracebacks."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in log messages (e.g., '[s3]', 'error', 'failed')",
                },
                "logger_name": {
                    "type": "string",
                    "description": "Filter by logger name (e.g., 'storage', 'discord', 'tools')",
                },
                "level": {
                    "type": "string",
                    "enum": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                    "description": "Filter by log level",
                },
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to search (default: 24)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 50, max: 200)",
                },
            },
            "required": [],
        },
        handler=search_logs,
    ),
    ToolDef(
        name="get_recent_logs",
        description=(
            "Get the most recent log entries. "
            "Useful for seeing what just happened or monitoring activity."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of entries to retrieve (default: 30, max: 100)",
                },
                "logger_name": {
                    "type": "string",
                    "description": "Optional: filter by logger name",
                },
            },
            "required": [],
        },
        handler=get_recent_logs,
    ),
    ToolDef(
        name="get_error_logs",
        description=(
            "Get recent errors and exceptions with full tracebacks. "
            "Use this when something failed or to check system health."
        ),
        parameters={
            "type": "object",
            "properties": {
                "hours": {
                    "type": "integer",
                    "description": "How many hours back to search (default: 24)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum errors to return (default: 20, max: 50)",
                },
                "include_warnings": {
                    "type": "boolean",
                    "description": "Include WARNING level logs (default: false)",
                },
            },
            "required": [],
        },
        handler=get_error_logs,
    ),
]


# --- Lifecycle Hooks ---


async def initialize() -> None:
    """Initialize system logs module with database connection."""
    global _session_factory

    try:
        from db import SessionLocal
        _session_factory = SessionLocal
        print("[system_logs] Loaded - connected to database")
    except Exception as e:
        print(f"[system_logs] Warning: Could not connect to database: {e}")


async def cleanup() -> None:
    """Cleanup on module unload."""
    global _session_factory
    _session_factory = None
