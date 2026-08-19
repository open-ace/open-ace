"""Add tenant_sensitive_keywords and tenant_keywords_version tables

Issue #2789: Custom sensitive keywords persistence and tenant isolation.

This migration adds:
- tenant_sensitive_keywords table for tenant-scoped keyword storage
- tenant_keywords_version table for multi-process cache consistency
- Indexes for efficient queries
- Foreign key constraints with cascade delete

Revision ID: 20260819_002
Revises: 20260819_001_add_anomaly_identity
Create Date: 2026-08-19

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260819_002"
down_revision = "20260819_001_add_anomaly_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add tenant_sensitive_keywords and tenant_keywords_version tables."""
    # Get database type for conditional logic
    bind = op.get_bind()
    dialect = bind.dialect.name

    # Check if tables already exist (concurrent migration safety)
    inspector = sa.inspect(bind)
    table_names = inspector.get_table_names()

    # ========================================================================
    # tenant_sensitive_keywords table
    # ========================================================================
    if "tenant_sensitive_keywords" not in table_names:
        if dialect == "sqlite":
            # SQLite version
            op.create_table(
                "tenant_sensitive_keywords",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("tenant_id", sa.Integer(), nullable=False),
                sa.Column("keyword", sa.Text(), nullable=False),
                sa.Column("normalized_keyword", sa.Text(), nullable=False),
                sa.Column("is_enabled", sa.Boolean(), server_default="1", nullable=False),
                sa.Column("created_by", sa.Integer(), nullable=True),
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                    nullable=False,
                ),
                sa.Column("updated_at", sa.DateTime(), nullable=True),
                sa.ForeignKeyConstraint(
                    ["tenant_id"],
                    ["tenants.id"],
                    ondelete="CASCADE",
                ),
                sa.ForeignKeyConstraint(
                    ["created_by"],
                    ["users.id"],
                    ondelete="SET NULL",
                ),
                sa.UniqueConstraint(
                    "tenant_id",
                    "normalized_keyword",
                    name="uq_tenant_keyword",
                ),
            )
        else:
            # PostgreSQL version
            op.create_table(
                "tenant_sensitive_keywords",
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("tenant_id", sa.Integer(), nullable=False),
                sa.Column("keyword", sa.Text(), nullable=False),
                sa.Column("normalized_keyword", sa.Text(), nullable=False),
                sa.Column("is_enabled", sa.Boolean(), server_default="true", nullable=False),
                sa.Column("created_by", sa.Integer(), nullable=True),
                sa.Column(
                    "created_at",
                    sa.DateTime(),
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                    nullable=False,
                ),
                sa.Column("updated_at", sa.DateTime(), nullable=True),
                sa.ForeignKeyConstraint(
                    ["tenant_id"],
                    ["tenants.id"],
                    ondelete="CASCADE",
                ),
                sa.ForeignKeyConstraint(
                    ["created_by"],
                    ["users.id"],
                    ondelete="SET NULL",
                ),
                sa.UniqueConstraint(
                    "tenant_id",
                    "normalized_keyword",
                    name="uq_tenant_keyword",
                ),
            )

        # Create indexes for tenant_sensitive_keywords
        op.create_index(
            "idx_tenant_keywords_tenant",
            "tenant_sensitive_keywords",
            ["tenant_id"],
            unique=False,
        )

        # Partial index for enabled keywords (high-frequency query)
        # Use 'true' for both dialects - SQLite supports 'true' as boolean literal
        # and schema-sync normalization will handle it consistently
        op.create_index(
            "idx_tenant_keywords_enabled",
            "tenant_sensitive_keywords",
            ["tenant_id", "is_enabled"],
            unique=False,
            postgresql_where=sa.text("is_enabled = true"),
            sqlite_where=sa.text("is_enabled = true"),
        )

    # ========================================================================
    # tenant_keywords_version table
    # ========================================================================
    if "tenant_keywords_version" not in table_names:
        if dialect == "sqlite":
            # SQLite version
            op.create_table(
                "tenant_keywords_version",
                sa.Column("tenant_id", sa.Integer(), primary_key=True),
                sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
                sa.Column(
                    "updated_at",
                    sa.DateTime(),
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                    nullable=False,
                ),
                sa.ForeignKeyConstraint(
                    ["tenant_id"],
                    ["tenants.id"],
                    ondelete="CASCADE",
                ),
            )
        else:
            # PostgreSQL version
            op.create_table(
                "tenant_keywords_version",
                sa.Column("tenant_id", sa.Integer(), primary_key=True),
                sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
                sa.Column(
                    "updated_at",
                    sa.DateTime(),
                    server_default=sa.text("CURRENT_TIMESTAMP"),
                    nullable=False,
                ),
                sa.ForeignKeyConstraint(
                    ["tenant_id"],
                    ["tenants.id"],
                    ondelete="CASCADE",
                ),
            )


def downgrade() -> None:
    """Remove tenant_sensitive_keywords and tenant_keywords_version tables."""
    # Drop tenant_keywords_version table
    op.drop_table("tenant_keywords_version")

    # Drop indexes for tenant_sensitive_keywords
    op.drop_index("idx_tenant_keywords_enabled", table_name="tenant_sensitive_keywords")
    op.drop_index("idx_tenant_keywords_tenant", table_name="tenant_sensitive_keywords")

    # Drop tenant_sensitive_keywords table
    op.drop_table("tenant_sensitive_keywords")
