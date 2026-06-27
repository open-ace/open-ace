"""Initialize project_categories with default data

Revision ID: 20260627_001_init_project_categories
Revises: 20260626_004_fix_tenant_quotas_overflow
Create Date: 2026-06-27

Issue: #1327
Initialize project_categories table with default categories.
This ensures projects are properly categorized instead of all showing as "uncategorized".

Default categories:
- Frontend: projects with frontend/web/ui keywords
- Backend: projects with backend/api/server keywords
- Testing: projects with test/tests/spec keywords
"""

import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)

revision: str = "20260627_001_init_project_categories"
down_revision: Union[str, None] = "20260626_004_fix_tenant_quotas_overflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Insert default project categories."""
    conn = op.get_bind()

    # Check if data already exists to avoid duplicates
    result = conn.execute(sa.text("SELECT COUNT(*) as count FROM project_categories"))
    row = result.fetchone()
    count = row[0] if row else 0

    if count > 0:
        logger.info(f"project_categories already has {count} records, skipping initialization")
        return

    # Insert default categories
    if conn.dialect.name == "postgresql":
        # PostgreSQL uses TRUE/FALSE for boolean
        op.execute(
            """
            INSERT INTO project_categories (name, key_patterns, sort_order, is_active)
            VALUES
                ('Frontend', '["frontend", "web", "ui"]', 1, TRUE),
                ('Backend', '["backend", "api", "server"]', 2, TRUE),
                ('Testing', '["test", "tests", "spec"]', 3, TRUE)
            """
        )
    else:
        # SQLite uses 1/0 for boolean
        op.execute(
            """
            INSERT INTO project_categories (name, key_patterns, sort_order, is_active)
            VALUES
                ('Frontend', '["frontend", "web", "ui"]', 1, 1),
                ('Backend', '["backend", "api", "server"]', 2, 1),
                ('Testing', '["test", "tests", "spec"]', 3, 1)
            """
        )

    logger.info("Initialized project_categories with default categories: Frontend, Backend, Testing")


def downgrade() -> None:
    """Remove default project categories."""
    # Delete the default categories we inserted
    op.execute(
        """
        DELETE FROM project_categories
        WHERE name IN ('Frontend', 'Backend', 'Testing')
        AND key_patterns IN ('["frontend", "web", "ui"]', '["backend", "api", "server"]', '["test", "tests", "spec"]')
        """
    )

    logger.info("Removed default project categories: Frontend, Backend, Testing")
