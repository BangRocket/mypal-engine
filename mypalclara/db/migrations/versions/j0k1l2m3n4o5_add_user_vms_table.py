"""add user_vms table for per-user VM tracking

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j0k1l2m3n4o5"
down_revision: Union[str, Sequence[str], None] = "i9j0k1l2m3n4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Check if table already exists (create_all may have made it)
    result = conn.execute(sa.text("SELECT 1 FROM information_schema.tables WHERE table_name = 'user_vms'"))
    if result.fetchone():
        return

    op.create_table(
        "user_vms",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("instance_name", sa.String(), nullable=False),
        sa.Column("instance_type", sa.String(), nullable=False, server_default="container"),
        sa.Column("status", sa.String(), nullable=False, server_default="provisioning"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("suspended_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_user_vms_user_id", "user_vms", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_user_vms_user_id", table_name="user_vms")
    op.drop_table("user_vms")
