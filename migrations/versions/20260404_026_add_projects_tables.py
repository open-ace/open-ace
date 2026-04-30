"""Add projects and user_projects tables for project management

Revision ID: 026_add_projects
Revises: 025_rename_linux_account_to_system_account
Create Date: 2026-04-04

This migration creates tables for project management and statistics:
- projects: Store project information (path, name, creator, etc.)
- user_projects: Track user-project relationships and usage statistics
- Adds project_id to daily_stats for project-level analytics

"""

from typing import Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "026_add_projects"
down_revision: Union[str, None] = "025_rename_linux_account_to_system_account"
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


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM information_schema.columns "
                "WHERE table_name = :table_name AND column_name = :column_name)"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(
                "SELECT name FROM pragma_table_info(:table_name) WHERE name = :column_name"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    # Create projects table
    if not _table_exists(conn, "projects"):
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("path", sa.String(500), nullable=False),
            sa.Column("name", sa.String(200), nullable=True),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("created_by", sa.Integer, nullable=True),  # user_id of creator
            sa.Column(
                "created_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
            sa.Column("is_shared", sa.Boolean, nullable=False, server_default=sa.text("0")),
        )

        # Create indexes for projects table
        op.create_index("idx_projects_path", "projects", ["path"])
        op.create_index("idx_projects_created_by", "projects", ["created_by"])
        op.create_index("idx_projects_is_active", "projects", ["is_active"])

        # Create unique constraint for path
        if conn.dialect.name == "postgresql":
            op.create_unique_constraint("uq_projects_path", "projects", ["path"])
        else:
            # SQLite: create unique index instead
            op.create_index("uq_projects_path", "projects", ["path"], unique=True)

    # Create user_projects table
    if not _table_exists(conn, "user_projects"):
        op.create_table(
            "user_projects",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, nullable=False),
            sa.Column("project_id", sa.Integer, nullable=False),
            sa.Column(
                "first_access_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "last_access_at",
                sa.DateTime,
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("total_sessions", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column("total_tokens", sa.BigInteger, nullable=False, server_default=sa.text("0")),
            sa.Column("total_requests", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column(
                "total_duration_seconds",
                sa.Integer,
                nullable=False,
                server_default=sa.text("0"),
            ),
        )

        # Create indexes for user_projects table
        op.create_index("idx_user_projects_user", "user_projects", ["user_id"])
        op.create_index("idx_user_projects_project", "user_projects", ["project_id"])

        # Create unique constraint for user_id + project_id
        if conn.dialect.name == "postgresql":
            op.create_unique_constraint(
                "uq_user_projects_user_project", "user_projects", ["user_id", "project_id"]
            )
        else:
            # SQLite: create unique index instead
            op.create_index(
                "uq_user_projects_user_project",
                "user_projects",
                ["user_id", "project_id"],
                unique=True,
            )

    # Add project_id and project_path columns to daily_stats
    if _table_exists(conn, "daily_stats"):
        if not _column_exists(conn, "daily_stats", "project_id"):
            op.add_column("daily_stats", sa.Column("project_id", sa.Integer, nullable=True))

        if not _column_exists(conn, "daily_stats", "project_path"):
            op.add_column(
                "daily_stats", sa.Column("project_path", sa.String(500), nullable=True)
            )

        # Create index for project_id
        if not _column_exists(conn, "daily_stats", "project_id"):
            op.create_index("idx_daily_stats_project", "daily_stats", ["project_id"])


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    # Remove project columns from daily_stats
    if _table_exists(conn, "daily_stats"):
        if _column_exists(conn, "daily_stats", "project_id"):
            op.drop_index("idx_daily_stats_project", "daily_stats")
            op.drop_column("daily_stats", "project_id")

        if _column_exists(conn, "daily_stats", "project_path"):
            op.drop_column("daily_stats", "project_path")

    # Drop user_projects table
    if _table_exists(conn, "user_projects"):
        op.drop_index("uq_user_projects_user_project", "user_projects")
        op.drop_index("idx_user_projects_project", "user_projects")
        op.drop_index("idx_user_projects_user", "user_projects")
        op.drop_table("user_projects")

    # Drop projects table
    if _table_exists(conn, "projects"):
        op.drop_index("uq_projects_path", "projects")
        op.drop_index("idx_projects_is_active", "projects")
        op.drop_index("idx_projects_created_by", "projects")
        op.drop_index("idx_projects_path", "projects")
        op.drop_table("projects")