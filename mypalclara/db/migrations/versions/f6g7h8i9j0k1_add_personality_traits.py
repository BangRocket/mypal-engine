"""add personality traits

Revision ID: f6g7h8i9j0k1
Revises: e5f6g7h8i9j0
Create Date: 2026-02-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "f6g7h8i9j0k1"
down_revision: Union[str, Sequence[str], None] = "e5f6g7h8i9j0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if "personality_traits" not in existing_tables:
        op.create_table(
            "personality_traits",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("agent_id", sa.String(), nullable=False, server_default="clara"),
            sa.Column("category", sa.String(50), nullable=False),
            sa.Column("trait_key", sa.String(100), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("source", sa.String(20), nullable=False, server_default="self"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_personality_trait_agent_category",
            "personality_traits",
            ["agent_id", "category"],
        )
        op.create_index(
            "ix_personality_trait_agent_active",
            "personality_traits",
            ["agent_id", "active"],
        )

    if "personality_trait_history" not in existing_tables:
        op.create_table(
            "personality_trait_history",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "trait_id",
                sa.String(),
                sa.ForeignKey("personality_traits.id"),
                nullable=False,
            ),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("event", sa.String(20), nullable=False),
            sa.Column("old_content", sa.Text(), nullable=True),
            sa.Column("new_content", sa.Text(), nullable=True),
            sa.Column("old_category", sa.String(50), nullable=True),
            sa.Column("new_category", sa.String(50), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("source", sa.String(20), nullable=False, server_default="self"),
            sa.Column("trigger_context", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_personality_trait_history_trait_id",
            "personality_trait_history",
            ["trait_id"],
        )
        op.create_index(
            "ix_personality_history_agent_created",
            "personality_trait_history",
            ["agent_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_table("personality_trait_history")
    op.drop_table("personality_traits")
