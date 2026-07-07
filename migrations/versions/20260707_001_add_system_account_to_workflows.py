"""Add system_account column to autonomous_workflows

Revision ID: 20260707_001_add_system_account_to_workflows
Revises: 20260704_001_session_messages_pagination_index
Create Date: 2026-07-07

Issue: #1530
When get_milestone_diff executes git show, GitHubOps runs as the service
process user (openace), which cannot access user private directories
(e.g. /home/<user> with 700 permissions). The workflow should store the
user's system_account at creation time so GitHubOps can use sudo -u to
execute git commands with proper permissions.

Column:
- system_account: TEXT - the user's system_account for sudo execution
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260707_001_add_system_account_to_workflows"
down_revision: Union[str, None] = "20260704_001_session_messages_pagination_index"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Check if column already exists (SQLite baseline may already have it)
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_columns = {col["name"] for col in inspector.get_columns("autonomous_workflows")}

    # Add system_account column for sudo execution
    if "system_account" not in existing_columns:
        op.add_column(
            "autonomous_workflows",
            sa.Column("system_account", sa.Text(), nullable=True, server_default=""),
        )

    # Backfill existing workflows with user's system_account
    # Only update where user has a system_account (skip admin users without one)
    dialect = connection.dialect.name
    if dialect == "sqlite":
        op.execute(
            """
            UPDATE autonomous_workflows
            SET system_account = (
                SELECT u.system_account FROM users u
                WHERE u.id = autonomous_workflows.user_id
                AND u.system_account IS NOT NULL
                AND u.system_account != ''
            )
            WHERE system_account IS NULL OR system_account = ''
            AND user_id IS NOT NULL
            """
        )
    else:  # PostgreSQL
        op.execute(
            """
            UPDATE autonomous_workflows aw
            SET system_account = u.system_account
            FROM users u
            WHERE aw.user_id = u.id
            AND aw.system_account IS NULL OR aw.system_account = ''
            AND u.system_account IS NOT NULL
            AND u.system_account != ''
            """
        )


def downgrade() -> None:
    # Use batch_alter_table for SQLite compatibility (DROP COLUMN requires batch mode)
    with op.batch_alter_table("autonomous_workflows") as batch_op:
        batch_op.drop_column("system_account")