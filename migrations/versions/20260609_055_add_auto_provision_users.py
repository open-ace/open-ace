"""Add auto_provision_users column to tenant_settings

Revision ID: 055_add_auto_provision_users
Revises: 054_add_ai_agent_settings_table
Create Date: 2026-06-09

This migration adds auto_provision_users column to tenant_settings table
to support automatic user provisioning when SSO login succeeds.

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "055_add_auto_provision_users"
down_revision: Union[str, None] = "054_add_ai_agent_settings_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Add auto_provision_users column to tenant_settings table
    op.add_column(
        "tenant_settings",
        sa.Column("auto_provision_users", sa.Integer(), server_default="0"),
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_column("tenant_settings", "auto_provision_users")
