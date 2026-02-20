"""add_context_id_to_sessions

Revision ID: 050c8b4a1a73
Revises:
Create Date: 2026-01-24 15:54:11.204602

Adds context_id column to sessions table for platform-agnostic session identification.
Existing sessions are backfilled with "default" context.

context_id examples:
- Discord: "channel-{channel_id}" or "dm-{user_id}"
- CLI: "cli" or "cli-{terminal_session}"
- Default: "default" for backward compatibility
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "050c8b4a1a73"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add context_id column to sessions table."""
    # Check if column already exists (model was updated before migration)
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if "sessions" not in inspector.get_table_names():
        # Table doesn't exist - it will be created with context_id by init_db()
        # Just mark migration as complete
        return

    # Check if column already exists
    columns = [c["name"] for c in inspector.get_columns("sessions")]
    if "context_id" in columns:
        # Column already exists - ensure index exists
        indexes = {idx["name"] for idx in inspector.get_indexes("sessions")}
        if "ix_session_user_context_project" not in indexes:
            op.create_index("ix_session_user_context_project", "sessions", ["user_id", "context_id", "project_id"])
        return

    # Add column as nullable first
    op.add_column("sessions", sa.Column("context_id", sa.String(), nullable=True))

    # Backfill existing sessions with 'default'
    op.execute("UPDATE sessions SET context_id = 'default' WHERE context_id IS NULL")

    # Now make it NOT NULL with server default
    # Note: SQLite doesn't support ALTER COLUMN, so we use batch operations
    with op.batch_alter_table("sessions") as batch_op:
        batch_op.alter_column("context_id", existing_type=sa.String(), nullable=False, server_default="default")

    # Add index for efficient lookups by user_id + context_id + project_id
    op.create_index("ix_session_user_context_project", "sessions", ["user_id", "context_id", "project_id"])


def downgrade() -> None:
    """Remove context_id column from sessions table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Check if table exists
    if "sessions" not in inspector.get_table_names():
        return

    # Check if index exists before dropping
    indexes = {idx["name"] for idx in inspector.get_indexes("sessions")}
    if "ix_session_user_context_project" in indexes:
        op.drop_index("ix_session_user_context_project", "sessions")

    # Check if column exists before dropping
    columns = [c["name"] for c in inspector.get_columns("sessions")]
    if "context_id" in columns:
        with op.batch_alter_table("sessions") as batch_op:
            batch_op.drop_column("context_id")
