"""Add cli_tools and cli_settings columns to api_key_store

Revision ID: 044_cli_settings
Revises: 043_add_user_avatar
Create Date: 2026-05-17

This allows API keys to be associated with CLI tools (claude-code, qwen-code)
and store custom settings.json configuration for each tool.

Issue: https://github.com/open-ace/open-ace/issues/414
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "044_cli_settings"
down_revision: Union[str, None] = "043_add_user_avatar"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Add cli_tools and cli_settings columns to api_key_store."""
    op.add_column(
        "api_key_store",
        sa.Column("cli_tools", sa.TEXT, nullable=True, default=None),
    )
    op.add_column(
        "api_key_store",
        sa.Column("cli_settings", sa.TEXT, nullable=True, default=None),
    )


def downgrade() -> None:
    """Remove cli_tools and cli_settings columns."""
    op.drop_column("api_key_store", "cli_settings")
    op.drop_column("api_key_store", "cli_tools")
