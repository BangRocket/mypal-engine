"""rename agent_id clara to mypalclara

Revision ID: g7h8i9j0k1l2
Revises: f6g7h8i9j0k1
Create Date: 2026-02-07 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, Sequence[str], None] = "f6g7h8i9j0k1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE intentions SET agent_id = 'mypalclara' WHERE agent_id = 'clara'")
    op.execute("UPDATE personality_traits SET agent_id = 'mypalclara' WHERE agent_id = 'clara'")
    op.execute("UPDATE personality_trait_history SET agent_id = 'mypalclara' WHERE agent_id = 'clara'")


def downgrade() -> None:
    op.execute("UPDATE intentions SET agent_id = 'clara' WHERE agent_id = 'mypalclara'")
    op.execute("UPDATE personality_traits SET agent_id = 'clara' WHERE agent_id = 'mypalclara'")
    op.execute("UPDATE personality_trait_history SET agent_id = 'clara' WHERE agent_id = 'mypalclara'")
