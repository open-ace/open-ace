"""Fix user_permissions.granted_at type from text to timestamp

Revision ID: 045_granted_at_type
Revises: 044_cli_settings
Create Date: 2026-05-23

user_permissions.granted_at was text, inconsistent with
machine_assignments.granted_at which is timestamp.

This migration also handles fresh databases where user_permissions
table doesn't exist yet (was only in schema-postgres.sql, no migration).
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "045_granted_at_type"
down_revision: Union[str, None] = "044_cli_settings"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _table_exists(conn, table_name: str) -> bool:
    """Check if a table exists in the database."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)"
            ),
            {"table_name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name = :table_name"),
            {"table_name": table_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Create user_permissions table if not exists, ensure granted_at is timestamp."""
    conn = op.get_bind()

    if not _table_exists(conn, "user_permissions"):
        # Table doesn't exist - create it with correct schema
        op.create_table(
            "user_permissions",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("permission", sa.Text, nullable=False),
            sa.Column("granted_by", sa.Integer, nullable=True),
            sa.Column("granted_at", sa.TIMESTAMP, nullable=True),
        )
        op.create_unique_constraint(
            "user_permissions_user_id_permission_key",
            "user_permissions",
            ["user_id", "permission"],
        )
    else:
        # Table exists - check if granted_at column is text type
        if conn.dialect.name == "postgresql":
            result = conn.execute(
                sa.text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'user_permissions' AND column_name = 'granted_at'"
                )
            )
            row = result.fetchone()
            if row and row[0] in ("text", "character varying"):
                op.alter_column(
                    "user_permissions",
                    "granted_at",
                    type_=sa.TIMESTAMP(),
                    existing_type=sa.TEXT(),
                    postgresql_using="granted_at::timestamp without time zone",
                )


def downgrade() -> None:
    """Revert granted_at back to text."""
    conn = op.get_bind()

    if _table_exists(conn, "user_permissions"):
        op.alter_column(
            "user_permissions",
            "granted_at",
            type_=sa.TEXT(),
            existing_type=sa.TIMESTAMP(),
        )
