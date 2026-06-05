"""Fix project path unique constraint for soft-delete support

Revision ID: 051_fix_project_path_unique
Revises: 050_session_type_index
Create Date: 2026-06-05

This migration fixes Issue #119: soft-deleted projects block creating new projects
with the same path due to unconditional unique constraint.

Problem:
- projects table has unconditional UNIQUE constraint on path
- Soft delete sets is_active=FALSE but record remains
- Cannot create new project with same path as soft-deleted project

Solution:
- Replace unconditional unique constraint with conditional unique index
- PostgreSQL: CREATE UNIQUE INDEX ... WHERE is_active IS TRUE
- SQLite: Use triggers to enforce conditional uniqueness

Impact:
- Allows re-creating projects with same path after soft delete
- Maintains uniqueness for active projects
- Preserves soft-deleted records for history

"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "051_fix_project_path_unique"
down_revision: Union[str, None] = "050_session_type_index"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _index_exists(conn, index_name: str, table_name: str) -> bool:
    """Check if an index exists."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT FROM pg_indexes WHERE indexname = :index_name)"
            ),
            {"index_name": index_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type='index' AND name = :index_name"
            ),
            {"index_name": index_name},
        )
        return result.fetchone() is not None


def _trigger_exists(conn, trigger_name: str) -> bool:
    """Check if a trigger exists (SQLite only)."""
    result = conn.execute(
        sa.text(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name = :trigger_name"
        ),
        {"trigger_name": trigger_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Upgrade database schema."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # PostgreSQL: Use conditional unique index (partial index)
        # Drop the old unconditional unique constraint
        if _index_exists(conn, "uq_projects_path", "projects"):
            op.drop_constraint("uq_projects_path", "projects", type_="unique")

        # Create new conditional unique index
        # Only enforce uniqueness for active projects (is_active = TRUE)
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_projects_path ON projects (path) WHERE is_active IS TRUE"
            )
        )

    else:
        # SQLite: Does not support conditional indexes
        # Solution: Drop unique constraint and use triggers to enforce conditional uniqueness

        # Step 1: Drop the old unique index
        if _index_exists(conn, "uq_projects_path", "projects"):
            op.drop_index("uq_projects_path", "projects")

        # Step 2: Create a regular (non-unique) index for query performance
        if not _index_exists(conn, "idx_projects_path", "projects"):
            op.create_index("idx_projects_path", "projects", ["path"])

        # Step 3: Create triggers to enforce conditional uniqueness
        # Trigger for INSERT: Check if path already exists in active projects
        if not _trigger_exists(conn, "trg_projects_path_unique_insert"):
            op.execute(
                sa.text(
                    """
                    CREATE TRIGGER trg_projects_path_unique_insert
                    BEFORE INSERT ON projects
                    FOR EACH ROW
                    WHEN NEW.is_active = 1
                    BEGIN
                        SELECT RAISE(ABORT, 'Path already exists for an active project')
                        WHERE EXISTS (
                            SELECT 1 FROM projects
                            WHERE path = NEW.path AND is_active = 1
                        );
                    END;
                    """
                )
            )

        # Trigger for UPDATE: Check if new path conflicts with existing active projects
        # Also handles re-activating a soft-deleted project
        if not _trigger_exists(conn, "trg_projects_path_unique_update"):
            op.execute(
                sa.text(
                    """
                    CREATE TRIGGER trg_projects_path_unique_update
                    BEFORE UPDATE ON projects
                    FOR EACH ROW
                    WHEN (NEW.is_active = 1 AND (OLD.is_active = 0 OR NEW.path != OLD.path))
                    BEGIN
                        SELECT RAISE(ABORT, 'Path already exists for an active project')
                        WHERE EXISTS (
                            SELECT 1 FROM projects
                            WHERE path = NEW.path AND is_active = 1 AND id != NEW.id
                        );
                    END;
                    """
                )
            )


def downgrade() -> None:
    """Downgrade database schema."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # Drop conditional unique index
        if _index_exists(conn, "uq_projects_path", "projects"):
            op.drop_index("uq_projects_path", "projects")

        # Restore unconditional unique constraint
        op.create_unique_constraint("uq_projects_path", "projects", ["path"])

    else:
        # SQLite: Drop triggers
        if _trigger_exists(conn, "trg_projects_path_unique_insert"):
            op.execute(sa.text("DROP TRIGGER trg_projects_path_unique_insert"))

        if _trigger_exists(conn, "trg_projects_path_unique_update"):
            op.execute(sa.text("DROP TRIGGER trg_projects_path_unique_update"))

        # Drop regular index
        if _index_exists(conn, "idx_projects_path", "projects"):
            op.drop_index("idx_projects_path", "projects")

        # Restore unconditional unique index
        op.create_index("uq_projects_path", "projects", ["path"], unique=True)