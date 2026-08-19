"""Add tenant_id to anomaly_status for tenant isolation.

Issue #2748: Audit analysis cross-tenant aggregation.

Revision ID: 20260819_001
Revises: 20260818_002
Create Date: 2026-08-19

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260819_001"
down_revision = "20260818_002_add_tool_account_mapping_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add tenant_id column to anomaly_status table."""
    # Check if we're on PostgreSQL or SQLite
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Add tenant_id column (nullable initially to allow existing data)
        op.add_column("anomaly_status", sa.Column("tenant_id", sa.Integer(), nullable=True))

        # Add foreign key constraint
        op.create_foreign_key(
            "anomaly_status_tenant_id_fkey",
            "anomaly_status",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="SET NULL",
        )

        # Drop old unique index
        op.drop_index("ix_anomaly_status_type_hash", table_name="anomaly_status")

        # Create new composite unique index including tenant_id
        op.create_index(
            "ix_anomaly_status_type_hash_tenant",
            "anomaly_status",
            ["anomaly_type", "affected_users_hash", "tenant_id"],
            unique=True,
        )

        # Backfill tenant_id from processed_by user's tenant
        op.execute("""
            UPDATE anomaly_status
            SET tenant_id = (
                SELECT u.tenant_id
                FROM users u
                WHERE u.id = anomaly_status.processed_by
            )
            WHERE processed_by IS NOT NULL
            """)

    else:  # SQLite
        # Add tenant_id column
        op.add_column("anomaly_status", sa.Column("tenant_id", sa.Integer(), nullable=True))

        # Drop old unique index
        op.drop_index("ix_anomaly_status_type_hash", table_name="anomaly_status")

        # Create new composite unique index including tenant_id
        op.create_index(
            "ix_anomaly_status_type_hash_tenant",
            "anomaly_status",
            ["anomaly_type", "affected_users_hash", "tenant_id"],
            unique=True,
        )


def downgrade() -> None:
    """Remove tenant_id column from anomaly_status table."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        # Drop new index
        op.drop_index("ix_anomaly_status_type_hash_tenant", table_name="anomaly_status")

        # Recreate old index
        op.create_index(
            "ix_anomaly_status_type_hash",
            "anomaly_status",
            ["anomaly_type", "affected_users_hash"],
            unique=True,
        )

        # Drop foreign key constraint
        op.drop_constraint("anomaly_status_tenant_id_fkey", "anomaly_status", type_="foreignkey")

        # Drop tenant_id column
        op.drop_column("anomaly_status", "tenant_id")

    else:  # SQLite
        # Drop new index
        op.drop_index("ix_anomaly_status_type_hash_tenant", table_name="anomaly_status")

        # Recreate old index
        op.create_index(
            "ix_anomaly_status_type_hash",
            "anomaly_status",
            ["anomaly_type", "affected_users_hash"],
            unique=True,
        )

        # Drop tenant_id column
        op.drop_column("anomaly_status", "tenant_id")
