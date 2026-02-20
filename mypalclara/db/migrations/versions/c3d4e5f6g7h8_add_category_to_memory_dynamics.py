"""add category to memory dynamics

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-02-05 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """Add category column to memory_dynamics."""
    if not column_exists("memory_dynamics", "category"):
        op.add_column(
            "memory_dynamics",
            sa.Column("category", sa.String(50), nullable=True, default=None),
        )


def downgrade() -> None:
    """Remove category column from memory_dynamics."""
    if column_exists("memory_dynamics", "category"):
        op.drop_column("memory_dynamics", "category")
