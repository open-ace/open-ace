"""Add avatar_url column to users table

Revision ID: 043_add_user_avatar
Revises: 042_add_anomaly_status
Create Date: 2026-05-13

Adds avatar_url column to users table for user profile avatar support.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "043_add_user_avatar"
down_revision: Union[str, None] = "042_add_anomaly_status"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_url", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_url")
