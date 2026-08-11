"""Add acceptance_verification columns to autonomous_workflows (#2335).

Revision ID: 20260805_010_acceptance_verification_columns
Revises: 20260805_001_add_max_changed_files_override
Create Date: 2026-08-05
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260805_010_acceptance_verification_columns"
down_revision = "20260805_001_add_max_changed_files_override"
branch_labels = None
depends_on = None

# (column, type, nullable). All nullable — existing/completed workflows keep
# NULL (verification only runs for workflows that reach merge after this change).
# report/snapshot are Text (JSON-encoded) for SQLite parity.
COLUMNS: list[tuple[str, sa.types.TypeEngine, bool]] = [
    ("verification_status", sa.Text(), True),
    ("verification_merge_sha", sa.Text(), True),
    ("verification_started_at", sa.DateTime(timezone=True), True),
    ("verification_completed_at", sa.DateTime(timezone=True), True),
    ("verification_attempt", sa.Integer(), True),
    ("verification_report", sa.Text(), True),
    ("issue_acceptance_snapshot", sa.Text(), True),
    ("issue_acceptance_hash", sa.Text(), True),
    ("verified_by", sa.Text(), True),
    ("verification_session_id", sa.Text(), True),
    ("issue_closed_by_workflow_at", sa.DateTime(timezone=True), True),
]


def upgrade() -> None:
    """Idempotent: skip columns that already exist.

    Prod schema may be ahead of the alembic stamp via direct SQL ALTER — see the
    server-deploy memory.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c["name"] for c in inspector.get_columns("autonomous_workflows")}
    for name, type_, nullable in COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "autonomous_workflows",
            sa.Column(name, type_, nullable=nullable),
        )


def downgrade() -> None:
    """Downgrade: drop columns (PostgreSQL) or no-op (SQLite).

    SQLite does not support DROP COLUMN before version 3.35.0 (2021-03-12).
    Batch mode is not suitable for production data preservation.
    For SQLite, we skip column drops and rely on schema migration
    during next upgrade.
    """
    bind = op.get_bind()
    dialect = bind.dialect.name

    # SQLite: skip DROP COLUMN operations
    if dialect == "sqlite":
        # SQLite doesn't support DROP COLUMN in older versions
        # Columns will remain but are harmless (nullable)
        return

    # PostgreSQL: proceed with DROP COLUMN
    for name, _, _ in reversed(COLUMNS):
        op.drop_column("autonomous_workflows", name)
