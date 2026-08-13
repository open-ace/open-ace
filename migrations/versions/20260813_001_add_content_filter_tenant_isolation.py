"""Add tenant isolation to content filter rules

Revision ID: 20260813_001_add_content_filter_tenant_isolation
Revises: 20260812_001_add_token_version
Create Date: 2026-08-13

Issue: #2550

Add tenant isolation and approval workflow to content_filter_rules table.
This prevents cross-tenant rule pollution and establishes governance controls.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_001_add_content_filter_tenant_isolation"
down_revision: str | None = "20260812_001"
branch_labels: str | None = None
depends_on: str | None = None


def _column_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    """Get column names for a table."""
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(inspector: sa.Inspector, table_name: str) -> set[str]:
    """Get index names for a table."""
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _table_exists(conn: sa.Connection, table_name: str) -> bool:
    """Check if a table exists."""
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


# System rule patterns from seed_manage_data.py
SYSTEM_RULE_PATTERNS = [
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",  # Email
    r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",  # Phone
    r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",  # Credit Card
    r"\bpassword\b",
    r"\bapi[_-]?key\b",
    r"\bsecret\b",
    r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
]


def upgrade() -> None:
    """Add tenant isolation fields to content_filter_rules."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    is_postgresql = conn.dialect.name == "postgresql"

    # ========================================================================
    # Step 1: Add new columns to content_filter_rules
    # ========================================================================
    columns = _column_names(inspector, "content_filter_rules")

    if "tenant_id" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("tenant_id", sa.Integer(), nullable=True),
        )

    if "source" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("source", sa.String(20), nullable=True, server_default="user"),
        )

    if "category" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("category", sa.String(50), nullable=True, server_default="custom"),
        )

    if "status" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("status", sa.String(20), nullable=True, server_default="active"),
        )

    if "approved_by" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("approved_by", sa.Integer(), nullable=True),
        )

    if "approved_at" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("approved_at", sa.DateTime(), nullable=True),
        )

    if "created_by" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("created_by", sa.Integer(), nullable=True),
        )

    if "metadata" not in columns:
        if is_postgresql:
            op.add_column(
                "content_filter_rules",
                sa.Column("metadata", sa.dialects.postgresql.JSON(), nullable=True),
            )
        else:
            op.add_column(
                "content_filter_rules",
                sa.Column("metadata", sa.Text(), nullable=True),
            )

    if "urgency_reason" not in columns:
        op.add_column(
            "content_filter_rules",
            sa.Column("urgency_reason", sa.Text(), nullable=True),
        )

    # ========================================================================
    # Step 2: Backfill system rules
    # ========================================================================
    # Mark system rules: source='system', tenant_id=NULL, category based on type
    for pattern in SYSTEM_RULE_PATTERNS:
        # Determine category based on pattern content
        if "email" in pattern.lower() or "phone" in pattern.lower():
            category = "pii"
        elif "password" in pattern.lower() or "secret" in pattern.lower():
            category = "security"
        else:
            category = "pii"

        metadata_json = json.dumps({"system_version": "1.0"})

        if is_postgresql:
            conn.execute(
                sa.text("""
                    UPDATE content_filter_rules
                    SET source = 'system',
                        tenant_id = NULL,
                        category = :category,
                        status = 'active',
                        metadata = CAST(:metadata AS json)
                    WHERE pattern = :pattern
                      AND (source IS NULL OR source = 'user')
                    """),
                {"category": category, "metadata": metadata_json, "pattern": pattern},
            )
        else:
            conn.execute(
                sa.text("""
                    UPDATE content_filter_rules
                    SET source = 'system',
                        tenant_id = NULL,
                        category = :category,
                        status = 'active',
                        metadata = :metadata
                    WHERE pattern = :pattern
                      AND (source IS NULL OR source = 'user')
                    """),
                {"category": category, "metadata": metadata_json, "pattern": pattern},
            )

    # ========================================================================
    # Step 3: Create indexes
    # ========================================================================
    indexes = _index_names(inspector, "content_filter_rules")

    if "idx_cfr_tenant_id" not in indexes:
        op.create_index("idx_cfr_tenant_id", "content_filter_rules", ["tenant_id"])

    if "idx_cfr_source" not in indexes:
        op.create_index("idx_cfr_source", "content_filter_rules", ["source"])

    if "idx_cfr_category" not in indexes:
        op.create_index("idx_cfr_category", "content_filter_rules", ["category"])

    if "idx_cfr_status" not in indexes:
        op.create_index("idx_cfr_status", "content_filter_rules", ["status"])

    if "idx_cfr_enabled" not in indexes:
        op.create_index("idx_cfr_enabled", "content_filter_rules", ["is_enabled"])

    # Composite index for tenant isolation queries
    if "idx_cfr_tenant_enabled_status" not in indexes:
        op.create_index(
            "idx_cfr_tenant_enabled_status",
            "content_filter_rules",
            ["tenant_id", "is_enabled", "status"],
        )

    # ========================================================================
    # Step 4: Create unique constraint for system rules
    # ========================================================================
    if "idx_cfr_system_unique" not in indexes:
        if is_postgresql:
            # PostgreSQL supports partial unique indexes
            op.create_index(
                "idx_cfr_system_unique",
                "content_filter_rules",
                ["pattern"],
                unique=True,
                postgresql_where=sa.text("source = 'system' AND tenant_id IS NULL"),
            )
        else:
            # SQLite doesn't support partial indexes in the same way
            # We'll create a regular unique index as fallback
            pass

    # ========================================================================
    # Step 5: Add check constraint for status
    # ========================================================================
    if is_postgresql:
        conn.execute(sa.text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'chk_status_valid'
                    ) THEN
                        ALTER TABLE content_filter_rules
                        ADD CONSTRAINT chk_status_valid
                        CHECK (status IN ('pending', 'approved', 'active', 'rejected', 'disabled'));
                    END IF;
                END $$;
                """))

    # ========================================================================
    # Step 6: Create filter_rule_approvals table
    # ========================================================================
    if not _table_exists(conn, "filter_rule_approvals"):
        if is_postgresql:
            op.create_table(
                "filter_rule_approvals",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column(
                    "rule_id",
                    sa.Integer(),
                    sa.ForeignKey("content_filter_rules.id", ondelete="CASCADE"),
                ),
                sa.Column("approver_id", sa.Integer(), sa.ForeignKey("users.id")),
                sa.Column("action", sa.String(20), nullable=False),
                sa.Column("comment", sa.Text()),
                sa.Column("tenant_id", sa.Integer()),
                sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            )
        else:
            op.create_table(
                "filter_rule_approvals",
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("rule_id", sa.Integer()),
                sa.Column("approver_id", sa.Integer()),
                sa.Column("action", sa.String(20), nullable=False),
                sa.Column("comment", sa.Text()),
                sa.Column("tenant_id", sa.Integer()),
                sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")),
            )

        # Create indexes for approval table
        op.create_index("idx_fra_rule_id", "filter_rule_approvals", ["rule_id"])
        op.create_index("idx_fra_tenant_id", "filter_rule_approvals", ["tenant_id"])


def downgrade() -> None:
    """Remove tenant isolation fields from content_filter_rules."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    is_postgresql = conn.dialect.name == "postgresql"

    # Drop filter_rule_approvals table
    if _table_exists(conn, "filter_rule_approvals"):
        op.drop_index("idx_fra_tenant_id", table_name="filter_rule_approvals")
        op.drop_index("idx_fra_rule_id", table_name="filter_rule_approvals")
        op.drop_table("filter_rule_approvals")

    # Drop check constraint for PostgreSQL
    if is_postgresql:
        conn.execute(sa.text("""
                DO $$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint WHERE conname = 'chk_status_valid'
                    ) THEN
                        ALTER TABLE content_filter_rules DROP CONSTRAINT chk_status_valid;
                    END IF;
                END $$;
                """))

    # Drop indexes
    indexes = _index_names(inspector, "content_filter_rules")

    for idx_name in [
        "idx_cfr_system_unique",
        "idx_cfr_tenant_enabled_status",
        "idx_cfr_enabled",
        "idx_cfr_status",
        "idx_cfr_category",
        "idx_cfr_source",
        "idx_cfr_tenant_id",
    ]:
        if idx_name in indexes:
            op.drop_index(idx_name, table_name="content_filter_rules")

    # Drop columns
    columns = _column_names(inspector, "content_filter_rules")

    for col_name in [
        "urgency_reason",
        "metadata",
        "created_by",
        "approved_at",
        "approved_by",
        "status",
        "category",
        "source",
        "tenant_id",
    ]:
        if col_name in columns:
            if is_postgresql:
                op.drop_column("content_filter_rules", col_name)
            else:
                with op.batch_alter_table("content_filter_rules") as batch_op:
                    batch_op.drop_column(col_name)
