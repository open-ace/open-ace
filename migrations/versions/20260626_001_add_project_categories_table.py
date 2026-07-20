"""Add project_categories table for categorized workspace grouping

Revision ID: 001_add_project_categories
Revises: 20260626_003_add_workflow_status_index
Create Date: 2026-06-26

Issue: #1278
Add project_categories table to support categorized workspace grouping display.
This allows administrators to predefine project categories with key patterns
for automatic workspace classification.

Fields:
- name: Category display name
- key_patterns: JSON array of key patterns for matching project paths
- sort_order: Display order
- is_active: Soft delete flag
"""

from collections.abc import Sequence

from alembic import op

revision: str = "001_add_project_categories"
down_revision: str | None = "20260626_003_add_workflow_status_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add project_categories table."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # PostgreSQL version
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS project_categories (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                key_patterns TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_categories_sort_order ON project_categories (sort_order)"
        )
    else:
        # SQLite version
        op.execute(
            """
            CREATE TABLE IF NOT EXISTS project_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                key_patterns TEXT NOT NULL,
                sort_order INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_categories_sort_order ON project_categories (sort_order)"
        )


def downgrade() -> None:
    """Remove project_categories table."""
    op.execute("DROP TABLE IF EXISTS project_categories")
