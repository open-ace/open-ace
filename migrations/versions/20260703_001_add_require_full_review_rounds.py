"""Add require_full_review_rounds column to autonomous_workflows

Revision ID: 20260703_001_add_require_full_review_rounds
Revises: 20260630_001_add_retry_count_column
Create Date: 2026-07-03

Issue: add workflow-level toggle to force planning/PR review to consume the
configured maximum rounds instead of stopping early after approval.
"""

import logging
from typing import Union

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260703_001_add_require_full_review_rounds"
down_revision: Union[str, None] = "20260627_001_add_model_gateway_config"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT EXISTS ("
                "  SELECT FROM information_schema.columns "
                "  WHERE table_name = :table_name AND column_name = :column_name"
                ")"
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        return result.fetchone()[0]
    result = conn.execute(
        sa.text("SELECT name FROM pragma_table_info(:table_name) WHERE name = :column_name"),
        {"table_name": table_name, "column_name": column_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    """Add require_full_review_rounds column to autonomous_workflows."""
    conn = op.get_bind()

    if not _column_exists(conn, "autonomous_workflows", "require_full_review_rounds"):
        log.info("Adding require_full_review_rounds column to autonomous_workflows table")
        op.add_column(
            "autonomous_workflows",
            sa.Column(
                "require_full_review_rounds",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    else:
        log.info("require_full_review_rounds column already exists, skipping")


def downgrade() -> None:
    """Remove require_full_review_rounds column."""
    conn = op.get_bind()

    if _column_exists(conn, "autonomous_workflows", "require_full_review_rounds"):
        log.info("Removing require_full_review_rounds column from autonomous_workflows table")
        if conn.dialect.name == "postgresql":
            op.drop_column("autonomous_workflows", "require_full_review_rounds")
        else:
            with op.batch_alter_table("autonomous_workflows") as batch_op:
                batch_op.drop_column("require_full_review_rounds")
    else:
        log.info("require_full_review_rounds column does not exist, skipping")
