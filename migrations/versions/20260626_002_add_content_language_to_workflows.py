"""Add content_language column to autonomous_workflows

Persists the language a workflow's AI-authored content is generated in
(plan / review / tldr / PR-review summaries). It is the source of truth for
workflow-authored content and does NOT depend on a viewer's current UI
language — persisted content is generated once at creation time and rendered
verbatim. System-authored structured content (e.g. progress_reported) is
rendered from structured payloads instead, see orchestrator i18n notes.

Revision ID: 20260626_002_workflow_content_language
Revises: 20260626_001_add_run_timeline_tables
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "20260626_002_workflow_content_language"
down_revision: str | None = "20260626_001_add_run_timeline_tables"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column already exists on the given table."""
    if conn.dialect.name == "postgresql":
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        )
        return result.scalar() > 0

    result = conn.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result.fetchall())


def upgrade() -> None:
    """Add content_language column to autonomous_workflows."""
    conn = op.get_bind()
    if not _column_exists(conn, "autonomous_workflows", "content_language"):
        op.add_column(
            "autonomous_workflows",
            sa.Column("content_language", sa.Text, server_default="en", nullable=False),
        )


def downgrade() -> None:
    """Remove content_language column from autonomous_workflows."""
    conn = op.get_bind()
    if _column_exists(conn, "autonomous_workflows", "content_language"):
        op.drop_column("autonomous_workflows", "content_language")
