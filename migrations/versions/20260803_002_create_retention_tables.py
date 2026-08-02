"""Create retention policy and execution tables.

Issue #2188 Phase 1: Create tables for retention policies, executions,
legal holds, evidence, archive files, and recycle bin.

Revision ID: 20260803_002_create_retention_tables
Revises: 20260803_001_add_retention_indexes
Create Date: 2026-08-03

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence


# revision identifiers, used by Alembic.
revision: str = "20260803_002_create_retention_tables"
down_revision: str | None = "20260803_001_add_retention_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    """Check if we're running on PostgreSQL."""
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    """Create retention policy and execution tables.

    Tables created:
    - retention_policies: Persistent retention policy configuration
    - retention_executions: Execution history with batch recovery support
    - legal_holds: Legal hold management
    - retention_evidence: Detailed evidence records
    - archive_files: Archive file index and lifecycle
    - recycle_bin: Deleted data recovery mechanism
    """
    # 1. retention_policies table
    op.create_table(
        "retention_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("data_type", sa.String(50), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("archive_target", sa.String(50), nullable=True),
        sa.Column("archive_config", sa.Text(), nullable=True),
        sa.Column("anonymize_fields", sa.Text(), nullable=True),
        sa.Column("backup_before_anonymize", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "data_type", "version"),
    )
    op.create_index("idx_retention_policies_tenant", "retention_policies", ["tenant_id"])
    op.create_index("idx_retention_policies_enabled", "retention_policies", ["enabled"])
    op.create_index("idx_retention_policies_data_type", "retention_policies", ["data_type"])

    # 2. retention_executions table
    op.create_table(
        "retention_executions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("lock_acquired_at", sa.DateTime(), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(), nullable=True),
        sa.Column("records_scanned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_archived", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_anonymized", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_in_recycle_bin", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column("batch_size", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("last_batch_id", sa.Integer(), nullable=True),
        sa.Column("total_batches", sa.Integer(), nullable=True),
        sa.Column("last_batch_status", sa.String(20), nullable=True),
        sa.Column("max_records_override", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["policy_id"], ["retention_policies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id"),
    )
    op.create_index("idx_retention_executions_tenant", "retention_executions", ["tenant_id"])
    op.create_index("idx_retention_executions_status", "retention_executions", ["status"])
    op.create_index("idx_retention_executions_execution_id", "retention_executions", ["execution_id"])
    op.create_index("idx_retention_executions_lock", "retention_executions", ["lock_acquired_at", "lock_expires_at"])

    # 3. legal_holds table
    op.create_table(
        "legal_holds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("hold_type", sa.String(20), nullable=False),
        sa.Column("data_type", sa.String(50), nullable=True),
        sa.Column("record_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("case_reference", sa.String(200), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("lifted_by", sa.Integer(), nullable=True),
        sa.Column("lifted_at", sa.DateTime(), nullable=True),
        sa.Column("lift_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_legal_holds_tenant", "legal_holds", ["tenant_id"])
    op.create_index("idx_legal_holds_active", "legal_holds", [sa.text("lifted_at IS NULL")], postgresql_where=sa.text("lifted_at IS NULL"))
    op.create_index("idx_legal_holds_data_type", "legal_holds", ["data_type"])

    # 4. retention_evidence table
    op.create_table(
        "retention_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(50), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before_count", sa.Integer(), nullable=True),
        sa.Column("after_count", sa.Integer(), nullable=True),
        sa.Column("records_affected", sa.Integer(), nullable=True),
        sa.Column("cutoff_date", sa.DateTime(), nullable=False),
        sa.Column("archive_location", sa.Text(), nullable=True),
        sa.Column("archive_checksum", sa.String(64), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_sample", sa.Text(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("policy_config", sa.Text(), nullable=True),
        sa.Column("policy_source", sa.String(20), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["execution_id"], ["retention_executions.execution_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_retention_evidence_execution", "retention_evidence", ["execution_id"])
    op.create_index("idx_retention_evidence_tenant", "retention_evidence", ["tenant_id"])
    op.create_index("idx_retention_evidence_timestamp", "retention_evidence", ["created_at"])

    # 5. archive_files table
    op.create_table(
        "archive_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("data_type", sa.String(50), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("verification_status", sa.String(20), nullable=True),
        sa.Column("source_deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["execution_id"], ["retention_executions.execution_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_archive_files_tenant", "archive_files", ["tenant_id"])
    op.create_index("idx_archive_files_expires", "archive_files", ["expires_at"])
    op.create_index("idx_archive_files_checksum", "archive_files", ["checksum"])
    op.create_index("idx_archive_files_batch", "archive_files", ["execution_id", "batch_id"])

    # 6. recycle_bin table
    op.create_table(
        "recycle_bin",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("execution_id", sa.String(64), nullable=False),
        sa.Column("data_type", sa.String(50), nullable=False),
        sa.Column("original_id", sa.Integer(), nullable=False),
        sa.Column("record_data", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("restored_at", sa.DateTime(), nullable=True),
        sa.Column("restored_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["retention_executions.execution_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_recycle_bin_tenant", "recycle_bin", ["tenant_id"])
    op.create_index("idx_recycle_bin_expires", "recycle_bin", ["expires_at"])
    op.create_index("idx_recycle_bin_execution", "recycle_bin", ["execution_id"])


def downgrade() -> None:
    """Remove retention tables."""
    op.drop_index("idx_recycle_bin_execution", table_name="recycle_bin")
    op.drop_index("idx_recycle_bin_expires", table_name="recycle_bin")
    op.drop_index("idx_recycle_bin_tenant", table_name="recycle_bin")
    op.drop_table("recycle_bin")

    op.drop_index("idx_archive_files_batch", table_name="archive_files")
    op.drop_index("idx_archive_files_checksum", table_name="archive_files")
    op.drop_index("idx_archive_files_expires", table_name="archive_files")
    op.drop_index("idx_archive_files_tenant", table_name="archive_files")
    op.drop_table("archive_files")

    op.drop_index("idx_retention_evidence_timestamp", table_name="retention_evidence")
    op.drop_index("idx_retention_evidence_tenant", table_name="retention_evidence")
    op.drop_index("idx_retention_evidence_execution", table_name="retention_evidence")
    op.drop_table("retention_evidence")

    op.drop_index("idx_legal_holds_data_type", table_name="legal_holds")
    op.drop_index("idx_legal_holds_active", table_name="legal_holds")
    op.drop_index("idx_legal_holds_tenant", table_name="legal_holds")
    op.drop_table("legal_holds")

    op.drop_index("idx_retention_executions_lock", table_name="retention_executions")
    op.drop_index("idx_retention_executions_execution_id", table_name="retention_executions")
    op.drop_index("idx_retention_executions_status", table_name="retention_executions")
    op.drop_index("idx_retention_executions_tenant", table_name="retention_executions")
    op.drop_table("retention_executions")

    op.drop_index("idx_retention_policies_data_type", table_name="retention_policies")
    op.drop_index("idx_retention_policies_enabled", table_name="retention_policies")
    op.drop_index("idx_retention_policies_tenant", table_name="retention_policies")
    op.drop_table("retention_policies")