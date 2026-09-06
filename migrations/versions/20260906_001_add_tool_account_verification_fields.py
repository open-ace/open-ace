"""Add verification fields to user_tool_accounts table

Issue #3273: Add verification_status, verification_result, and verified_at fields
to support tool account mapping verification functionality.

Revision ID: 20260906_001_add_tool_account_verification_fields
Revises: 20260905_001_add_encryption_keys_table
Create Date: 2026-09-06

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260906_001_add_tool_account_verification_fields"
down_revision = "20260905_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add verification fields to user_tool_accounts table."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "user_tool_accounts" in existing_tables:
        # Get existing columns
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("user_tool_accounts")}

        # Add verification_status field if not exist
        if "verification_status" not in existing_columns:
            op.add_column(
                "user_tool_accounts",
                sa.Column("verification_status", sa.String(50), nullable=True),
            )

        # Add verification_result field if not exist
        if "verification_result" not in existing_columns:
            op.add_column(
                "user_tool_accounts", sa.Column("verification_result", sa.Text(), nullable=True)
            )

        # Add verified_at field if not exist
        if "verified_at" not in existing_columns:
            op.add_column(
                "user_tool_accounts", sa.Column("verified_at", sa.DateTime(), nullable=True)
            )

        # Set default value for existing records
        # Only update if verification_status column exists and is NULL
        if "verification_status" in existing_columns:
            op.execute(
                "UPDATE user_tool_accounts SET verification_status = 'unverified' WHERE verification_status IS NULL"
            )


def downgrade() -> None:
    """Remove verification fields from user_tool_accounts table."""
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "user_tool_accounts" in existing_tables:
        conn = op.get_bind()
        inspector = sa.inspect(conn)
        existing_columns = {col["name"] for col in inspector.get_columns("user_tool_accounts")}

        for column in ["verified_at", "verification_result", "verification_status"]:
            if column in existing_columns:
                op.drop_column("user_tool_accounts", column)
