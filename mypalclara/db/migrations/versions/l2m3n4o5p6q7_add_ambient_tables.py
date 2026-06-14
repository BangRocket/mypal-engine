"""add ambient tables (surfaced_thoughts, ambient_user_config)

Revision ID: l2m3n4o5p6q7
Revises: k1l2m3n4o5p6
Create Date: 2026-06-14 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l2m3n4o5p6q7"
down_revision: Union[str, Sequence[str], None] = "k1l2m3n4o5p6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has("surfaced_thoughts"):
        op.create_table(
            "surfaced_thoughts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False, server_default="queue"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("surfaced_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("delivered", sa.String(), nullable=False, server_default="false"),
        )
        op.create_index("ix_surfaced_thoughts_user_id", "surfaced_thoughts", ["user_id"])
    if not _has("ambient_user_config"):
        op.create_table(
            "ambient_user_config",
            sa.Column("user_id", sa.String(), primary_key=True),
            sa.Column("reflection_opt_in", sa.String(), nullable=False, server_default="false"),
            sa.Column("timezone", sa.String(), nullable=True),
            sa.Column("last_dm_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("ambient_user_config")
    op.drop_index("ix_surfaced_thoughts_user_id", table_name="surfaced_thoughts")
    op.drop_table("surfaced_thoughts")
