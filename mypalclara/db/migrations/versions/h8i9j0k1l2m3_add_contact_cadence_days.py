"""add contact_cadence_days to user_interaction_patterns

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-02-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h8i9j0k1l2m3"
down_revision: Union[str, Sequence[str], None] = "g7h8i9j0k1l2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'user_interaction_patterns' AND column_name = 'contact_cadence_days'"
        )
    )
    if not result.fetchone():
        op.add_column("user_interaction_patterns", sa.Column("contact_cadence_days", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("user_interaction_patterns", "contact_cadence_days")
