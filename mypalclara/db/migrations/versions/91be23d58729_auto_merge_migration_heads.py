"""auto-merge migration heads

Revision ID: 91be23d58729
Revises: b040b33ec24e, i9j0k1l2m3n4
Create Date: 2026-02-09 22:35:01.648717

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "91be23d58729"
down_revision: Union[str, Sequence[str], None] = ("b040b33ec24e", "i9j0k1l2m3n4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
