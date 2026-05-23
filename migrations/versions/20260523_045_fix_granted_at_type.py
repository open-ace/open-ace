"""Fix user_permissions.granted_at type from text to timestamp

Revision ID: 045_granted_at_type
Revises: 044_cli_settings
Create Date: 2026-05-23

user_permissions.granted_at was text, inconsistent with
machine_assignments.granted_at which is timestamp.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_granted_at_type"
down_revision: Union[str, None] = "044_cli_settings"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    """Change granted_at from text to timestamp."""
    op.alter_column(
        "user_permissions",
        "granted_at",
        type_=sa.TIMESTAMP(),
        existing_type=sa.TEXT(),
        postgresql_using="granted_at::timestamp without time zone",
    )


def downgrade() -> None:
    """Revert granted_at back to text."""
    op.alter_column(
        "user_permissions",
        "granted_at",
        type_=sa.TEXT(),
        existing_type=sa.TIMESTAMP(),
    )
