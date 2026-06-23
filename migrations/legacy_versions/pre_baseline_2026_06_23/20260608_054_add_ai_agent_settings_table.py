"""Add ai_agent_settings table

Revision ID: 054_add_ai_agent_settings_table
Revises: 053_agent_identity_tables
Create Date: 2026-06-08

This migration creates an ai_agent_settings table to store AI agent
configuration such as the GitHub account used by autonomous workflows.
Separate from security_settings for semantic clarity and independent API.

"""

import sqlalchemy as sa
from alembic import op

revision: str = "054_add_ai_agent_settings_table"
down_revision: str = "053_agent_identity_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade database schema."""
    op.create_table(
        "ai_agent_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("setting_key", sa.String(100), nullable=False, unique=True),
        sa.Column("setting_value", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.TIMESTAMP(), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("idx_ai_agent_settings_key", "ai_agent_settings", ["setting_key"])

    # Insert default AI agent settings
    op.execute(
        """
        INSERT INTO ai_agent_settings (setting_key, setting_value, description) VALUES
        ('ai_github_token', '', 'GitHub PAT for the AI bot account (used by autonomous workflows)'),
        ('ai_github_author_name', 'Open ACE AI', 'Git commit author name for AI operations'),
        ('ai_github_author_email', 'bot@open-ace.com', 'Git commit author email for AI operations')
        ON CONFLICT(setting_key) DO NOTHING
        """
    )


def downgrade() -> None:
    """Downgrade database schema."""
    op.drop_index("idx_ai_agent_settings_key", "ai_agent_settings")
    op.drop_table("ai_agent_settings")
