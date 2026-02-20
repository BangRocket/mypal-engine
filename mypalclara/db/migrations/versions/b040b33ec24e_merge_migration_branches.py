"""merge migration branches

Revision ID: b040b33ec24e
Revises: 4502328fd583, b2c3d4e5f6g7
Create Date: 2026-02-04 15:22:25.584789

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b040b33ec24e"
down_revision: Union[str, Sequence[str], None] = ("4502328fd583", "b2c3d4e5f6g7")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
