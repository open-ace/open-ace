"""Add scope, priority, weight columns to api_key_store

Revision ID: 047_api_key_scope
Revises: 046_login_attempts
Create Date: 2026-05-28

Adds columns for unified API key management:
- scope: 'local', 'remote', or 'shared' (default 'remote')
- priority: higher = preferred (default 0)
- weight: for weighted random within same priority (default 100)

Issue: https://github.com/open-ace/open-ace/issues/593
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_api_key_scope"
down_revision: Union[str, None] = "046_login_attempts"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Add scope, priority, weight columns to api_key_store."""
    op.add_column(
        "api_key_store",
        sa.Column("scope", sa.TEXT, nullable=True, server_default="remote"),
    )
    op.add_column(
        "api_key_store",
        sa.Column("priority", sa.INTEGER, nullable=True, server_default="0"),
    )
    op.add_column(
        "api_key_store",
        sa.Column("weight", sa.INTEGER, nullable=True, server_default="100"),
    )


def downgrade() -> None:
    """Remove scope, priority, weight columns."""
    op.drop_column("api_key_store", "weight")
    op.drop_column("api_key_store", "priority")
    op.drop_column("api_key_store", "scope")
