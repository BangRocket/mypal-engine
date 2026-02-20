"""add tool audit logs

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6g7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create tool_audit_logs table for tool execution tracking
    op.create_table(
        "tool_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("tool_name", sa.String(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("parameters", sa.Text(), nullable=True),
        sa.Column("result_status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(), nullable=True),
        sa.Column("intent", sa.String(), nullable=True),
        sa.Column("channel_id", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tool_audit_logs_timestamp"), "tool_audit_logs", ["timestamp"], unique=False)
    op.create_index(op.f("ix_tool_audit_logs_user_id"), "tool_audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_tool_audit_logs_tool_name"), "tool_audit_logs", ["tool_name"], unique=False)
    op.create_index(op.f("ix_tool_audit_logs_channel_id"), "tool_audit_logs", ["channel_id"], unique=False)
    op.create_index("ix_tool_audit_user_time", "tool_audit_logs", ["user_id", "timestamp"], unique=False)
    op.create_index("ix_tool_audit_tool_time", "tool_audit_logs", ["tool_name", "timestamp"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_tool_audit_tool_time", table_name="tool_audit_logs")
    op.drop_index("ix_tool_audit_user_time", table_name="tool_audit_logs")
    op.drop_index(op.f("ix_tool_audit_logs_channel_id"), table_name="tool_audit_logs")
    op.drop_index(op.f("ix_tool_audit_logs_tool_name"), table_name="tool_audit_logs")
    op.drop_index(op.f("ix_tool_audit_logs_user_id"), table_name="tool_audit_logs")
    op.drop_index(op.f("ix_tool_audit_logs_timestamp"), table_name="tool_audit_logs")
    op.drop_table("tool_audit_logs")
