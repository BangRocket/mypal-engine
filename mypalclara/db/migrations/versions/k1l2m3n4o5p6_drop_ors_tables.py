"""drop ORS tables (proactive_notes, proactive_assessments, user_interaction_patterns)

Revision ID: k1l2m3n4o5p6
Revises: 506b1c1496b6
Create Date: 2026-06-14 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1l2m3n4o5p6"
down_revision: Union[str, Sequence[str], None] = "506b1c1496b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _drop_if_exists(table: str) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table in insp.get_table_names():
        op.drop_table(table)


def upgrade() -> None:
    _drop_if_exists("proactive_notes")
    _drop_if_exists("proactive_assessments")
    _drop_if_exists("user_interaction_patterns")


def downgrade() -> None:
    # Recreate minimal table shells so downgrade does not crash; ORS data is not restored.
    op.create_table(
        "user_interaction_patterns",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "proactive_notes",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
    )
    op.create_table(
        "proactive_assessments",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
    )
