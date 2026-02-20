"""reconcile schema drift

Reconciles the PostgreSQL schema with current SQLAlchemy models:
- canonical_users: add status, is_admin columns
- mcp_servers: migrate from inline-config schema to config-file-reference schema
- Drop orphan tables (deployment_events, dashboard_sessions) with no model references
- Remove duplicate indexes (idx_* vs ix_*) on proactive tables

Revision ID: 97e434d26526
Revises: 91be23d58729
Create Date: 2026-02-09 22:42:12.424232

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "97e434d26526"
down_revision: Union[str, Sequence[str], None] = "91be23d58729"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = :t"),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.columns " "WHERE table_name = :t AND column_name = :c"),
        {"t": table_name, "c": column_name},
    )
    return result.fetchone() is not None


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :i"),
        {"i": index_name},
    )
    return result.fetchone() is not None


def _constraint_exists(conn, constraint_name: str) -> bool:
    result = conn.execute(
        sa.text("SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = :c"),
        {"c": constraint_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()

    # --- Drop orphan tables (no model references these) ---
    if _table_exists(conn, "deployment_events"):
        if _index_exists(conn, "ix_deployment_events_workflow_run_id"):
            op.drop_index(op.f("ix_deployment_events_workflow_run_id"), table_name="deployment_events")
        op.drop_table("deployment_events")
    if _table_exists(conn, "dashboard_sessions"):
        if _index_exists(conn, "ix_dashboard_sessions_session_token"):
            op.drop_index(op.f("ix_dashboard_sessions_session_token"), table_name="dashboard_sessions")
        op.drop_table("dashboard_sessions")

    # --- canonical_users: add missing columns ---
    if not _column_exists(conn, "canonical_users", "status"):
        op.add_column("canonical_users", sa.Column("status", sa.String(), server_default="active", nullable=False))
    if not _column_exists(conn, "canonical_users", "is_admin"):
        op.add_column("canonical_users", sa.Column("is_admin", sa.Boolean(), server_default="0", nullable=False))

    # --- mcp_servers: migrate to config-file-reference schema ---
    # Step 1: Add new columns (server_type as nullable first for backfill)
    for col_name, col_type in [
        ("user_id", sa.String()),
        ("server_type", sa.String()),
        ("config_path", sa.String()),
        ("oauth_required", sa.Boolean()),
        ("oauth_token_id", sa.String()),
        ("total_tool_calls", sa.Integer()),
        ("last_used_at", sa.DateTime()),
    ]:
        if not _column_exists(conn, "mcp_servers", col_name):
            op.add_column("mcp_servers", sa.Column(col_name, col_type, nullable=True))

    # Step 2: Backfill server_type from existing data
    if _column_exists(conn, "mcp_servers", "server_type"):
        op.execute("UPDATE mcp_servers SET server_type = 'local' WHERE server_type IS NULL")
        # Step 3: Now make server_type NOT NULL
        op.alter_column("mcp_servers", "server_type", nullable=False)

    # Step 4: Alter nullable constraints to match model
    if _column_exists(conn, "mcp_servers", "source_type"):
        op.alter_column("mcp_servers", "source_type", existing_type=sa.VARCHAR(), nullable=True)
    op.alter_column("mcp_servers", "created_at", existing_type=postgresql.TIMESTAMP(), nullable=True)
    op.alter_column("mcp_servers", "updated_at", existing_type=postgresql.TIMESTAMP(), nullable=True)

    # Step 5: Update indexes
    if _index_exists(conn, "ix_mcp_servers_name"):
        op.drop_index(op.f("ix_mcp_servers_name"), table_name="mcp_servers")
    if not _index_exists(conn, "ix_mcp_server_enabled"):
        op.create_index("ix_mcp_server_enabled", "mcp_servers", ["enabled"], unique=False)
    if not _index_exists(conn, "ix_mcp_server_user_name"):
        op.create_index("ix_mcp_server_user_name", "mcp_servers", ["user_id", "name"], unique=False)
    if not _index_exists(conn, "ix_mcp_servers_user_id"):
        op.create_index(op.f("ix_mcp_servers_user_id"), "mcp_servers", ["user_id"], unique=False)
    if not _constraint_exists(conn, "fk_mcp_servers_oauth_token"):
        op.create_foreign_key(
            "fk_mcp_servers_oauth_token", "mcp_servers", "mcp_oauth_tokens", ["oauth_token_id"], ["id"]
        )

    # Step 6: Drop old inline-config columns (data stored in config files now)
    for col in [
        "args",
        "tools_json",
        "endpoint_url",
        "command",
        "cwd",
        "docker_config",
        "transport",
        "display_name",
        "env",
    ]:
        if _column_exists(conn, "mcp_servers", col):
            op.drop_column("mcp_servers", col)

    # --- Remove duplicate indexes (DB has both idx_* and ix_* variants) ---
    if _index_exists(conn, "idx_proactive_assessments_created_at"):
        op.drop_index("idx_proactive_assessments_created_at", table_name="proactive_assessments")
    if _index_exists(conn, "idx_proactive_assessments_user_id"):
        op.drop_index("idx_proactive_assessments_user_id", table_name="proactive_assessments")
    if _index_exists(conn, "idx_proactive_notes_user_id"):
        op.drop_index("idx_proactive_notes_user_id", table_name="proactive_notes")


def downgrade() -> None:
    """Downgrade schema."""
    # --- Restore duplicate indexes on proactive tables ---
    op.create_index("idx_proactive_notes_user_id", "proactive_notes", ["user_id"], unique=False)
    op.create_index("idx_proactive_assessments_user_id", "proactive_assessments", ["user_id"], unique=False)
    op.create_index("idx_proactive_assessments_created_at", "proactive_assessments", ["created_at"], unique=False)

    # --- Restore mcp_servers old schema ---
    op.add_column("mcp_servers", sa.Column("env", sa.TEXT(), nullable=True))
    op.add_column("mcp_servers", sa.Column("display_name", sa.VARCHAR(), nullable=True))
    op.add_column("mcp_servers", sa.Column("transport", sa.VARCHAR(), nullable=False, server_default="stdio"))
    op.add_column("mcp_servers", sa.Column("docker_config", sa.TEXT(), nullable=True))
    op.add_column("mcp_servers", sa.Column("cwd", sa.VARCHAR(), nullable=True))
    op.add_column("mcp_servers", sa.Column("command", sa.VARCHAR(), nullable=True))
    op.add_column("mcp_servers", sa.Column("endpoint_url", sa.VARCHAR(), nullable=True))
    op.add_column("mcp_servers", sa.Column("tools_json", sa.TEXT(), nullable=True))
    op.add_column("mcp_servers", sa.Column("args", sa.TEXT(), nullable=True))
    op.drop_constraint("fk_mcp_servers_oauth_token", "mcp_servers", type_="foreignkey")
    op.drop_index(op.f("ix_mcp_servers_user_id"), table_name="mcp_servers")
    op.drop_index("ix_mcp_server_user_name", table_name="mcp_servers")
    op.drop_index("ix_mcp_server_enabled", table_name="mcp_servers")
    op.create_index(op.f("ix_mcp_servers_name"), "mcp_servers", ["name"], unique=True)
    op.alter_column("mcp_servers", "updated_at", existing_type=postgresql.TIMESTAMP(), nullable=False)
    op.alter_column("mcp_servers", "created_at", existing_type=postgresql.TIMESTAMP(), nullable=False)
    op.alter_column("mcp_servers", "source_type", existing_type=sa.VARCHAR(), nullable=False)
    op.drop_column("mcp_servers", "last_used_at")
    op.drop_column("mcp_servers", "total_tool_calls")
    op.drop_column("mcp_servers", "oauth_token_id")
    op.drop_column("mcp_servers", "oauth_required")
    op.drop_column("mcp_servers", "config_path")
    op.drop_column("mcp_servers", "server_type")
    op.drop_column("mcp_servers", "user_id")

    # --- Remove canonical_users columns ---
    op.drop_column("canonical_users", "is_admin")
    op.drop_column("canonical_users", "status")

    # --- Restore orphan tables ---
    op.create_table(
        "dashboard_sessions",
        sa.Column("id", sa.VARCHAR(), nullable=False),
        sa.Column("session_token", sa.VARCHAR(), nullable=False),
        sa.Column("github_user_id", sa.INTEGER(), nullable=False),
        sa.Column("github_username", sa.VARCHAR(), nullable=False),
        sa.Column("github_access_token", sa.TEXT(), nullable=False),
        sa.Column("avatar_url", sa.VARCHAR(), nullable=True),
        sa.Column("is_collaborator", sa.VARCHAR(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dashboard_sessions_session_token", "dashboard_sessions", ["session_token"], unique=True)
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.VARCHAR(), nullable=False),
        sa.Column("workflow_run_id", sa.VARCHAR(), nullable=False),
        sa.Column("workflow_name", sa.VARCHAR(), nullable=False),
        sa.Column("from_branch", sa.VARCHAR(), nullable=False),
        sa.Column("to_branch", sa.VARCHAR(), nullable=False),
        sa.Column("triggered_by", sa.VARCHAR(), nullable=False),
        sa.Column("triggered_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("status", sa.VARCHAR(), nullable=True),
        sa.Column("conclusion", sa.VARCHAR(), nullable=True),
        sa.Column("head_sha", sa.VARCHAR(), nullable=True),
        sa.Column("commits_promoted", sa.INTEGER(), nullable=True),
        sa.Column("release_tag", sa.VARCHAR(), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), nullable=True),
        sa.Column("updated_at", postgresql.TIMESTAMP(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_events_workflow_run_id", "deployment_events", ["workflow_run_id"], unique=True)
