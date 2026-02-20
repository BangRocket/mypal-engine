"""merge memory_history and category branches

Revision ID: a86677195cf8
Revises: 4502328fd583, c3d4e5f6g7h8
Create Date: 2026-02-05 21:15:50.088691

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a86677195cf8"
down_revision: Union[str, Sequence[str], None] = ("4502328fd583", "c3d4e5f6g7h8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
