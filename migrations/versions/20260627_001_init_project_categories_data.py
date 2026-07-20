"""Initialize project_categories table (no default data)

Revision ID: 20260627_001_init_project_categories
Revises: 20260626_004_fix_tenant_quotas_overflow
Create Date: 2026-06-27

Issue: #1327 (original), #1382 (removal)
Originally initialized project_categories with default categories.
Now removed - Issue #1371's smart categorization handles project grouping.

Why removed:
- Default categories (Frontend/Backend/Testing) use functional keywords
- These don't match actual project paths like /home/openace, /home/openace/iplan
- Smart categorization (extractProjectName) extracts project names from paths
- Example: /home/openace → "openace", /home/openace/iplan → "iplan"

Migration kept for alembic version chain continuity.
"""

import logging
from collections.abc import Sequence

logger = logging.getLogger(__name__)

revision: str = "20260627_001_init_project_categories"
down_revision: str | None = "20260626_004_fix_tenant_quotas_overflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op: default categories removed, smart categorization handles grouping."""
    logger.info("Skipping default project categories initialization (Issue #1382)")


def downgrade() -> None:
    """No-op: nothing to remove since upgrade does nothing."""
    logger.info("No default project categories to remove (Issue #1382)")
