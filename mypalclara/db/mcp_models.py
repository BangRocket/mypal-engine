"""MCP (Model Context Protocol) database models.

These models complement the file-based MCP configs by providing:
- Per-user server associations (multi-user isolation)
- Tool call tracking and metrics
- OAuth token storage with user association
- Usage analytics and rate limiting support
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from mypalclara.db.base import Base


def utcnow():
    """Return current UTC time (naive, for SQLite compatibility)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_uuid() -> str:
    return str(uuid4())


class MCPServer(Base):
    """Tracks MCP server installations per user.

    This model provides multi-user isolation by associating
    servers with specific users or marking them as global.
    """

    __tablename__ = "mcp_servers"

    id = Column(String, primary_key=True, default=gen_uuid)

    # User association (null = global server available to all)
    user_id = Column(String, nullable=True, index=True)

    # Server identification
    name = Column(String, nullable=False)
    server_type = Column(String, nullable=False)  # "local" or "remote"
    source_type = Column(String, nullable=True)  # "npm", "smithery", "github", etc.
    source_url = Column(String, nullable=True)

    # Config path reference (file-based config still used)
    config_path = Column(String, nullable=True)

    # Status caching
    enabled = Column(Boolean, default=True)
    status = Column(String, default="stopped")  # stopped, running, error, pending_auth
    tool_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    last_error_at = Column(DateTime, nullable=True)

    # OAuth reference (for remote servers)
    oauth_required = Column(Boolean, default=False)
    oauth_token_id = Column(String, ForeignKey("mcp_oauth_tokens.id"), nullable=True)

    # Usage tracking
    total_tool_calls = Column(Integer, default=0)
    last_used_at = Column(DateTime, nullable=True)

    # Metadata
    installed_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Indexes for efficient queries
    __table_args__ = (
        Index("ix_mcp_server_user_name", "user_id", "name"),
        Index("ix_mcp_server_enabled", "enabled"),
    )

    # Relationships
    oauth_token = relationship("MCPOAuthToken", back_populates="server", uselist=False)
    tool_calls = relationship("MCPToolCall", back_populates="server")


class MCPOAuthToken(Base):
    """OAuth tokens for MCP servers (per-user).

    Stores OAuth credentials separately from the JSON config files,
    allowing per-user token isolation for hosted servers.
    """

    __tablename__ = "mcp_oauth_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)

    # User association
    user_id = Column(String, nullable=False, index=True)

    # Server identification
    server_name = Column(String, nullable=False)
    server_url = Column(String, nullable=True)

    # OAuth metadata
    authorization_endpoint = Column(String, nullable=True)
    token_endpoint = Column(String, nullable=True)
    registration_endpoint = Column(String, nullable=True)

    # Client registration
    client_id = Column(String, nullable=True)
    client_secret = Column(Text, nullable=True)  # Encrypted
    redirect_uri = Column(String, nullable=True)

    # PKCE state (temporary during auth flow)
    code_verifier = Column(String, nullable=True)
    state_token = Column(String, nullable=True)

    # Tokens (encrypted for security)
    access_token = Column(Text, nullable=True)  # Encrypted
    refresh_token = Column(Text, nullable=True)  # Encrypted
    token_type = Column(String, default="Bearer")
    expires_at = Column(DateTime, nullable=True)
    scopes = Column(Text, nullable=True)  # Space-separated scopes

    # Status
    status = Column(String, default="pending")  # pending, authorized, expired, error
    last_refresh_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    # Metadata
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Indexes
    __table_args__ = (Index("ix_mcp_oauth_user_server", "user_id", "server_name"),)

    # Relationships
    server = relationship("MCPServer", back_populates="oauth_token")

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.expires_at:
            return False
        # Consider expired 5 minutes before actual expiry
        from datetime import timedelta

        buffer = timedelta(minutes=5)
        return utcnow() >= (self.expires_at - buffer)


class MCPToolCall(Base):
    """Tracks MCP tool call history for metrics and debugging.

    Records each tool invocation with timing, success/failure,
    and optional result summary for analytics.
    """

    __tablename__ = "mcp_tool_calls"

    id = Column(String, primary_key=True, default=gen_uuid)

    # Context
    user_id = Column(String, nullable=False, index=True)
    session_id = Column(String, nullable=True, index=True)
    request_id = Column(String, nullable=True)  # Gateway request ID

    # Server and tool
    server_id = Column(String, ForeignKey("mcp_servers.id"), nullable=True)
    server_name = Column(String, nullable=False)
    tool_name = Column(String, nullable=False)

    # Call details
    arguments = Column(Text, nullable=True)  # JSON-encoded arguments (truncated)
    result_preview = Column(Text, nullable=True)  # First N chars of result

    # Timing
    started_at = Column(DateTime, default=utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Status
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    error_type = Column(String, nullable=True)  # timeout, connection, execution, etc.

    # Indexes for efficient queries
    __table_args__ = (
        Index("ix_mcp_tool_call_time", "started_at"),
        Index("ix_mcp_tool_call_server_tool", "server_name", "tool_name"),
        Index("ix_mcp_tool_call_user_time", "user_id", "started_at"),
    )

    # Relationships
    server = relationship("MCPServer", back_populates="tool_calls")


class MCPUsageMetrics(Base):
    """Aggregated usage metrics for MCP tools (per user per day).

    Pre-computed daily metrics for efficient rate limiting
    and usage analytics without scanning tool_calls.
    """

    __tablename__ = "mcp_usage_metrics"

    id = Column(String, primary_key=True, default=gen_uuid)

    # Dimensions
    user_id = Column(String, nullable=False)
    server_name = Column(String, nullable=False)
    date = Column(String, nullable=False)  # YYYY-MM-DD format

    # Metrics
    call_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    timeout_count = Column(Integer, default=0)
    total_duration_ms = Column(Integer, default=0)
    avg_duration_ms = Column(Float, default=0.0)

    # Tool breakdown (JSON: {"tool_name": count})
    tool_counts = Column(Text, nullable=True)

    # First/last usage for the day
    first_call_at = Column(DateTime, nullable=True)
    last_call_at = Column(DateTime, nullable=True)

    # Metadata
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Composite primary key equivalent via unique constraint
    __table_args__ = (
        Index("ix_mcp_metrics_unique", "user_id", "server_name", "date", unique=True),
        Index("ix_mcp_metrics_date", "date"),
    )


class MCPRateLimit(Base):
    """Rate limiting configuration and state for MCP tool calls.

    Allows per-user or global rate limits on specific servers or tools.
    """

    __tablename__ = "mcp_rate_limits"

    id = Column(String, primary_key=True, default=gen_uuid)

    # Scope (null = global)
    user_id = Column(String, nullable=True, index=True)
    server_name = Column(String, nullable=True)  # null = all servers
    tool_name = Column(String, nullable=True)  # null = all tools on server

    # Limits
    max_calls_per_minute = Column(Integer, nullable=True)
    max_calls_per_hour = Column(Integer, nullable=True)
    max_calls_per_day = Column(Integer, nullable=True)

    # Current window state
    current_minute_count = Column(Integer, default=0)
    current_hour_count = Column(Integer, default=0)
    current_day_count = Column(Integer, default=0)
    minute_window_start = Column(DateTime, nullable=True)
    hour_window_start = Column(DateTime, nullable=True)
    day_window_start = Column(DateTime, nullable=True)

    # Enabled flag
    enabled = Column(Boolean, default=True)

    # Metadata
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

    # Indexes
    __table_args__ = (Index("ix_mcp_rate_limit_scope", "user_id", "server_name", "tool_name"),)


# Export models for import in db/models.py
__all__ = [
    "MCPServer",
    "MCPOAuthToken",
    "MCPToolCall",
    "MCPUsageMetrics",
    "MCPRateLimit",
]
