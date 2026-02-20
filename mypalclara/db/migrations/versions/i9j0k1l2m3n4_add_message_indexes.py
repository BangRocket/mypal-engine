"""add composite index on messages (session_id, created_at)

Revision ID: i9j0k1l2m3n4
Revises: h8i9j0k1l2m3
Create Date: 2026-02-09 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i9j0k1l2m3n4"
down_revision: Union[str, Sequence[str], None] = "h8i9j0k1l2m3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT 1 FROM pg_indexes WHERE indexname = 'ix_message_session_created'"))
    if not result.fetchone():
        op.create_index("ix_message_session_created", "messages", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_message_session_created", table_name="messages")
