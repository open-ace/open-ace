"""add autonomous_workflows status index

Revision ID: 20260626_003_add_workflow_status_index
Revises: 20260626_002_workflow_content_language
Create Date: 2026-06-26

Adds a composite index on ``autonomous_workflows(status, created_at)`` to serve
the scheduler's hot polling queries:

- ``get_active_workflows``  -> ``WHERE status IN (...) ORDER BY created_at``
- ``get_paused_workflows``  -> ``WHERE status = 'paused' ORDER BY created_at``
- ``get_queued_workflows``  -> ``WHERE status = 'queued' ORDER BY created_at``

Without it, those status-filtered scans over the workflow table fall back to a
full scan + sort on each scheduler tick.

Implementation note (periodic review follow-up): the other indexes flagged in the
audit were verified as FALSE POSITIVES and intentionally NOT added here:
- ``autonomous_workflows(parent_workflow_id)`` already exists as ``idx_workflows_parent``
- ``daily_messages(date, tool_name, host_name)`` already exists as ``idx_messages_date_tool_host``
- ``agent_run_events`` single-column indexes already cover its queries

CONCURRENTLY handling: ``CREATE INDEX CONCURRENTLY`` cannot run inside a
transaction, so on PostgreSQL we wrap it in ``autocommit_block()`` and pass
``postgresql_concurrently=True``. That dialect kwarg is ignored by SQLite, where
a normal index is created instead — keeping the SQLite migration tests green.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260626_003_add_workflow_status_index"
down_revision: str | None = "20260626_002_workflow_content_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "idx_workflows_status_created"
TABLE = "autonomous_workflows"
COLUMNS = ["status", "created_at"]


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Create the status/created_at index (concurrently on PostgreSQL)."""
    if _is_postgresql():
        with op.get_context().autocommit_block():
            op.create_index(INDEX_NAME, TABLE, COLUMNS, postgresql_concurrently=True)
    else:
        op.create_index(INDEX_NAME, TABLE, COLUMNS)


def downgrade() -> None:
    """Drop the status/created_at index (concurrently on PostgreSQL)."""
    if _is_postgresql():
        with op.get_context().autocommit_block():
            op.drop_index(INDEX_NAME, table_name=TABLE, postgresql_concurrently=True)
    else:
        op.drop_index(INDEX_NAME, table_name=TABLE)
