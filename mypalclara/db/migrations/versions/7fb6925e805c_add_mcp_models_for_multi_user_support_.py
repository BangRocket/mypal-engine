"""add mcp models for multi-user support and metrics

Revision ID: 7fb6925e805c
Revises: 050c8b4a1a73
Create Date: 2026-02-01 20:08:47.982380

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "7fb6925e805c"
down_revision: Union[str, Sequence[str], None] = "050c8b4a1a73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    try:
        indexes = inspector.get_indexes(table_name)
        return any(idx["name"] == index_name for idx in indexes)
    except Exception:
        return False


def upgrade() -> None:
    """Upgrade schema."""
    # Create mcp_oauth_tokens
    if not table_exists("mcp_oauth_tokens"):
        op.create_table(
            "mcp_oauth_tokens",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("server_name", sa.String(), nullable=False),
            sa.Column("server_url", sa.String(), nullable=True),
            sa.Column("authorization_endpoint", sa.String(), nullable=True),
            sa.Column("token_endpoint", sa.String(), nullable=True),
            sa.Column("registration_endpoint", sa.String(), nullable=True),
            sa.Column("client_id", sa.String(), nullable=True),
            sa.Column("client_secret", sa.Text(), nullable=True),
            sa.Column("redirect_uri", sa.String(), nullable=True),
            sa.Column("code_verifier", sa.String(), nullable=True),
            sa.Column("state_token", sa.String(), nullable=True),
            sa.Column("access_token", sa.Text(), nullable=True),
            sa.Column("refresh_token", sa.Text(), nullable=True),
            sa.Column("token_type", sa.String(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("last_refresh_at", sa.DateTime(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if table_exists("mcp_oauth_tokens"):
        if not index_exists("mcp_oauth_tokens", "ix_mcp_oauth_tokens_user_id"):
            op.create_index(op.f("ix_mcp_oauth_tokens_user_id"), "mcp_oauth_tokens", ["user_id"], unique=False)
        if not index_exists("mcp_oauth_tokens", "ix_mcp_oauth_user_server"):
            op.create_index("ix_mcp_oauth_user_server", "mcp_oauth_tokens", ["user_id", "server_name"], unique=False)

    # Create mcp_rate_limits
    if not table_exists("mcp_rate_limits"):
        op.create_table(
            "mcp_rate_limits",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("server_name", sa.String(), nullable=True),
            sa.Column("tool_name", sa.String(), nullable=True),
            sa.Column("max_calls_per_minute", sa.Integer(), nullable=True),
            sa.Column("max_calls_per_hour", sa.Integer(), nullable=True),
            sa.Column("max_calls_per_day", sa.Integer(), nullable=True),
            sa.Column("current_minute_count", sa.Integer(), nullable=True),
            sa.Column("current_hour_count", sa.Integer(), nullable=True),
            sa.Column("current_day_count", sa.Integer(), nullable=True),
            sa.Column("minute_window_start", sa.DateTime(), nullable=True),
            sa.Column("hour_window_start", sa.DateTime(), nullable=True),
            sa.Column("day_window_start", sa.DateTime(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if table_exists("mcp_rate_limits"):
        if not index_exists("mcp_rate_limits", "ix_mcp_rate_limit_scope"):
            op.create_index(
                "ix_mcp_rate_limit_scope", "mcp_rate_limits", ["user_id", "server_name", "tool_name"], unique=False
            )
        if not index_exists("mcp_rate_limits", "ix_mcp_rate_limits_user_id"):
            op.create_index(op.f("ix_mcp_rate_limits_user_id"), "mcp_rate_limits", ["user_id"], unique=False)

    # Create mcp_usage_metrics
    if not table_exists("mcp_usage_metrics"):
        op.create_table(
            "mcp_usage_metrics",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("server_name", sa.String(), nullable=False),
            sa.Column("date", sa.String(), nullable=False),
            sa.Column("call_count", sa.Integer(), nullable=True),
            sa.Column("success_count", sa.Integer(), nullable=True),
            sa.Column("error_count", sa.Integer(), nullable=True),
            sa.Column("timeout_count", sa.Integer(), nullable=True),
            sa.Column("total_duration_ms", sa.Integer(), nullable=True),
            sa.Column("avg_duration_ms", sa.Float(), nullable=True),
            sa.Column("tool_counts", sa.Text(), nullable=True),
            sa.Column("first_call_at", sa.DateTime(), nullable=True),
            sa.Column("last_call_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    if table_exists("mcp_usage_metrics"):
        if not index_exists("mcp_usage_metrics", "ix_mcp_metrics_date"):
            op.create_index("ix_mcp_metrics_date", "mcp_usage_metrics", ["date"], unique=False)
        if not index_exists("mcp_usage_metrics", "ix_mcp_metrics_unique"):
            op.create_index(
                "ix_mcp_metrics_unique", "mcp_usage_metrics", ["user_id", "server_name", "date"], unique=True
            )

    # Create mcp_servers
    if not table_exists("mcp_servers"):
        op.create_table(
            "mcp_servers",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("server_type", sa.String(), nullable=False),
            sa.Column("source_type", sa.String(), nullable=True),
            sa.Column("source_url", sa.String(), nullable=True),
            sa.Column("config_path", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("tool_count", sa.Integer(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("last_error_at", sa.DateTime(), nullable=True),
            sa.Column("oauth_required", sa.Boolean(), nullable=True),
            sa.Column("oauth_token_id", sa.String(), nullable=True),
            sa.Column("total_tool_calls", sa.Integer(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("installed_by", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["oauth_token_id"],
                ["mcp_oauth_tokens.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    if table_exists("mcp_servers"):
        if not index_exists("mcp_servers", "ix_mcp_server_enabled"):
            op.create_index("ix_mcp_server_enabled", "mcp_servers", ["enabled"], unique=False)
        if not index_exists("mcp_servers", "ix_mcp_server_user_name"):
            op.create_index("ix_mcp_server_user_name", "mcp_servers", ["user_id", "name"], unique=False)
        if not index_exists("mcp_servers", "ix_mcp_servers_user_id"):
            op.create_index(op.f("ix_mcp_servers_user_id"), "mcp_servers", ["user_id"], unique=False)

    # Create mcp_tool_calls
    if not table_exists("mcp_tool_calls"):
        op.create_table(
            "mcp_tool_calls",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=True),
            sa.Column("server_id", sa.String(), nullable=True),
            sa.Column("server_name", sa.String(), nullable=False),
            sa.Column("tool_name", sa.String(), nullable=False),
            sa.Column("arguments", sa.Text(), nullable=True),
            sa.Column("result_preview", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("success", sa.Boolean(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("error_type", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(
                ["server_id"],
                ["mcp_servers.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    if table_exists("mcp_tool_calls"):
        if not index_exists("mcp_tool_calls", "ix_mcp_tool_call_server_tool"):
            op.create_index(
                "ix_mcp_tool_call_server_tool", "mcp_tool_calls", ["server_name", "tool_name"], unique=False
            )
        if not index_exists("mcp_tool_calls", "ix_mcp_tool_call_time"):
            op.create_index("ix_mcp_tool_call_time", "mcp_tool_calls", ["started_at"], unique=False)
        if not index_exists("mcp_tool_calls", "ix_mcp_tool_call_user_time"):
            op.create_index("ix_mcp_tool_call_user_time", "mcp_tool_calls", ["user_id", "started_at"], unique=False)
        if not index_exists("mcp_tool_calls", "ix_mcp_tool_calls_session_id"):
            op.create_index(op.f("ix_mcp_tool_calls_session_id"), "mcp_tool_calls", ["session_id"], unique=False)
        if not index_exists("mcp_tool_calls", "ix_mcp_tool_calls_user_id"):
            op.create_index(op.f("ix_mcp_tool_calls_user_id"), "mcp_tool_calls", ["user_id"], unique=False)

    # Drop legacy tables if they exist (not present on fresh databases)
    if table_exists("web_chat_messages"):
        if index_exists("web_chat_messages", "ix_web_chat_messages_session_id"):
            op.drop_index(op.f("ix_web_chat_messages_session_id"), table_name="web_chat_messages")
        op.drop_table("web_chat_messages")

    if table_exists("notes"):
        if index_exists("notes", "ix_notes_owner_id"):
            op.drop_index(op.f("ix_notes_owner_id"), table_name="notes")
        op.drop_table("notes")


def downgrade() -> None:
    """Downgrade schema."""
    # Recreate legacy tables
    if not table_exists("notes"):
        op.create_table(
            "notes",
            sa.Column("id", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("owner_id", sa.VARCHAR(), autoincrement=False, nullable=True),
            sa.Column("title", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("content", sa.TEXT(), autoincrement=False, nullable=True),
            sa.Column("created_by", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("updated_by", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
            sa.Column("updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("notes_pkey")),
        )
        op.create_index(op.f("ix_notes_owner_id"), "notes", ["owner_id"], unique=False)

    if not table_exists("web_chat_messages"):
        op.create_table(
            "web_chat_messages",
            sa.Column("id", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("session_id", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("user_id", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("role", sa.VARCHAR(), autoincrement=False, nullable=False),
            sa.Column("content", sa.TEXT(), autoincrement=False, nullable=False),
            sa.Column("tier", sa.VARCHAR(), autoincrement=False, nullable=True),
            sa.Column("created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False),
            sa.PrimaryKeyConstraint("id", name=op.f("web_chat_messages_pkey")),
        )
        op.create_index(op.f("ix_web_chat_messages_session_id"), "web_chat_messages", ["session_id"], unique=False)

    # Drop MCP tables
    if table_exists("mcp_tool_calls"):
        op.drop_index(op.f("ix_mcp_tool_calls_user_id"), table_name="mcp_tool_calls")
        op.drop_index(op.f("ix_mcp_tool_calls_session_id"), table_name="mcp_tool_calls")
        op.drop_index("ix_mcp_tool_call_user_time", table_name="mcp_tool_calls")
        op.drop_index("ix_mcp_tool_call_time", table_name="mcp_tool_calls")
        op.drop_index("ix_mcp_tool_call_server_tool", table_name="mcp_tool_calls")
        op.drop_table("mcp_tool_calls")

    if table_exists("mcp_servers"):
        op.drop_index(op.f("ix_mcp_servers_user_id"), table_name="mcp_servers")
        op.drop_index("ix_mcp_server_user_name", table_name="mcp_servers")
        op.drop_index("ix_mcp_server_enabled", table_name="mcp_servers")
        op.drop_table("mcp_servers")

    if table_exists("mcp_usage_metrics"):
        op.drop_index("ix_mcp_metrics_unique", table_name="mcp_usage_metrics")
        op.drop_index("ix_mcp_metrics_date", table_name="mcp_usage_metrics")
        op.drop_table("mcp_usage_metrics")

    if table_exists("mcp_rate_limits"):
        op.drop_index(op.f("ix_mcp_rate_limits_user_id"), table_name="mcp_rate_limits")
        op.drop_index("ix_mcp_rate_limit_scope", table_name="mcp_rate_limits")
        op.drop_table("mcp_rate_limits")

    if table_exists("mcp_oauth_tokens"):
        op.drop_index("ix_mcp_oauth_user_server", table_name="mcp_oauth_tokens")
        op.drop_index(op.f("ix_mcp_oauth_tokens_user_id"), table_name="mcp_oauth_tokens")
        op.drop_table("mcp_oauth_tokens")
