"""Add test retry counter columns to autonomous_workflows

Revision ID: 20260704_001_add_test_retry_columns
Revises: 20260703_002_add_sso_auth_states
Create Date: 2026-07-04

Issue: test-skip / test-fail retry counters (skip_retries, test_retries,
dev_retries_on_test_fail) are written via _update_workflow but were never
added to the table schema nor ALLOWED_WORKFLOW_FIELDS, so the writes were
silently filtered out and the scheduler re-read 0 on the next advance() —
causing the dev agent to be re-run on a test-only retry and then false-
failing with "agent produced no code changes" (commit SHA unchanged).

Columns (all INTEGER DEFAULT 0, nullable for back-compat with existing rows):
- test_retries: count of test-agent-failure retries (timeout/API error)
- skip_retries: count of test-skipped retries (agent didn't run tests)
- dev_retries_on_test_fail: count of dev reruns triggered by test failure
"""

import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger(__name__)

revision: str = "20260704_001_add_test_retry_columns"
down_revision: str | None = "20260703_002_add_sso_auth_states"
branch_labels: str | None = None
depends_on: str | None = None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table (PostgreSQL or SQLite)."""
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


_NEW_COLUMNS = [
    ("test_retries", sa.Integer()),
    ("skip_retries", sa.Integer()),
    ("dev_retries_on_test_fail", sa.Integer()),
]


def upgrade() -> None:
    """Add test retry counter columns to autonomous_workflows."""
    conn = op.get_bind()
    for col_name, col_type in _NEW_COLUMNS:
        if not _column_exists(conn, "autonomous_workflows", col_name):
            log.info("Adding %s column to autonomous_workflows", col_name)
            op.add_column(
                "autonomous_workflows",
                sa.Column(col_name, col_type, nullable=True, server_default="0"),
            )
        else:
            log.info("%s column already exists, skipping", col_name)


def downgrade() -> None:
    """Remove test retry counter columns from autonomous_workflows."""
    conn = op.get_bind()
    for col_name, _ in reversed(_NEW_COLUMNS):
        if not _column_exists(conn, "autonomous_workflows", col_name):
            continue
        log.info("Removing %s column from autonomous_workflows", col_name)
        if conn.dialect.name == "postgresql":
            op.drop_column("autonomous_workflows", col_name)
        else:
            with op.batch_alter_table("autonomous_workflows") as batch_op:
                batch_op.drop_column(col_name)
