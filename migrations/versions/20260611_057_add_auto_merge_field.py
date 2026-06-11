"""Add auto_merge field for batch workflow automation

Revision ID: 057_auto_merge
Revises: 056_hostname_indexes
Create Date: 2026-06-11

This migration adds the auto_merge field to autonomous_workflows table,
enabling batch workflows to automatically merge PRs and proceed to the next
workflow without manual intervention.

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "057_auto_merge"
down_revision: Union[str, None] = "056_hostname_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add auto_merge column with default TRUE."""
    op.add_column(
        "autonomous_workflows",
        sa.Column(
            "auto_merge",
            sa.Boolean,
            server_default=sa.text("TRUE"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Remove auto_merge column."""
    op.drop_column("autonomous_workflows", "auto_merge")
