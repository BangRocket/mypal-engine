"""auto-merge migration heads

Revision ID: 506b1c1496b6
Revises: a8b9c0d1e2f3, j0k1l2m3n4o5
Create Date: 2026-05-04 23:15:19.862647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '506b1c1496b6'
down_revision: Union[str, Sequence[str], None] = ('a8b9c0d1e2f3', 'j0k1l2m3n4o5')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
