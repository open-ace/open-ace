"""Merge multiple heads into single revision.

Revision ID: 20260623_merge_heads
Revises: 20260622_001_session_message_source, 026_add_tool_account_mapping_rules
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa

revision = "20260623_merge_heads"
down_revision = ("20260622_001_session_message_source", "026_add_tool_account_mapping_rules")
branch_labels = None
depends_on = None


def upgrade():
    pass  # Topological merge only - no schema changes


def downgrade():
    pass
