"""add web identity models

Revision ID: d4e5f6g7h8i9
Revises: a86677195cf8
Create Date: 2026-02-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "d4e5f6g7h8i9"
down_revision: Union[str, Sequence[str], None] = "a86677195cf8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    bind = op.get_bind()
    inspector = inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not table_exists("canonical_users"):
        op.create_table(
            "canonical_users",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("primary_email", sa.String(), nullable=True),
            sa.Column("avatar_url", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("primary_email"),
        )

    if not table_exists("platform_links"):
        op.create_table(
            "platform_links",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("canonical_user_id", sa.String(), sa.ForeignKey("canonical_users.id"), nullable=False),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("platform_user_id", sa.String(), nullable=False),
            sa.Column("prefixed_user_id", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("linked_at", sa.DateTime(), nullable=True),
            sa.Column("linked_via", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("prefixed_user_id"),
        )
        op.create_index(
            "ix_platform_link_platform_user",
            "platform_links",
            ["platform", "platform_user_id"],
            unique=True,
        )

    if not table_exists("oauth_tokens"):
        op.create_table(
            "oauth_tokens",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("canonical_user_id", sa.String(), sa.ForeignKey("canonical_users.id"), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("access_token", sa.Text(), nullable=False),
            sa.Column("refresh_token", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=True),
            sa.Column("provider_user_id", sa.String(), nullable=True),
            sa.Column("provider_data", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_oauth_token_user_provider",
            "oauth_tokens",
            ["canonical_user_id", "provider"],
            unique=True,
        )

    if not table_exists("web_sessions"):
        op.create_table(
            "web_sessions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("canonical_user_id", sa.String(), sa.ForeignKey("canonical_users.id"), nullable=False),
            sa.Column("session_token_hash", sa.String(), nullable=False),
            sa.Column("ip_address", sa.String(), nullable=True),
            sa.Column("user_agent", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("revoked", sa.Boolean(), nullable=True, default=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_token_hash"),
        )


def downgrade() -> None:
    op.drop_table("web_sessions")
    op.drop_table("oauth_tokens")
    op.drop_table("platform_links")
    op.drop_table("canonical_users")
