"""Fix is_group_chat column type to BOOLEAN

Revision ID: 010_fix_is_group_chat_type
Revises: 009_add_security_settings_table
Create Date: 2026-03-22

This migration fixes the is_group_chat column type in daily_messages table:
- Changes from INTEGER to BOOLEAN for PostgreSQL
- SQLite uses INTEGER for boolean (0/1) so no change needed

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "010_fix_is_group_chat_type"
down_revision: Union[str, None] = "009_add_security_settings_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade database schema."""
    # Get database connection
    conn = op.get_bind()

    # Check if we're using PostgreSQL
    if conn.dialect.name == "postgresql":
        # Alter column type from INTEGER to BOOLEAN
        op.execute(
            """
            ALTER TABLE daily_messages 
            ALTER COLUMN is_group_chat TYPE BOOLEAN 
            USING (CASE WHEN is_group_chat = 1 THEN TRUE ELSE FALSE END)
        """
        )


def downgrade() -> None:
    """Downgrade database schema."""
    # Get database connection
    conn = op.get_bind()

    # Check if we're using PostgreSQL
    if conn.dialect.name == "postgresql":
        # Revert column type from BOOLEAN to INTEGER
        op.execute(
            """
            ALTER TABLE daily_messages 
            ALTER COLUMN is_group_chat TYPE INTEGER 
            USING (CASE WHEN is_group_chat THEN 1 ELSE 0 END)
        """
        )
