"""Normalize legacy PostgreSQL audit_logs.success integer storage.

Some long-lived installations created this column from the SQLite-compatible
schema before the PostgreSQL baseline declared it BOOLEAN.  Current writers
correctly bind Python booleans, which PostgreSQL will not implicitly cast to an
INTEGER column.  Repair only the drifted shape; already-correct databases and
SQLite remain unchanged.
"""

from alembic import op


revision = "20260918_002_normalize_audit_log_success"
down_revision = "20260918_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'audit_logs'
                  AND column_name = 'success'
                  AND data_type = 'integer'
            ) THEN
                ALTER TABLE audit_logs ALTER COLUMN success DROP DEFAULT;
                ALTER TABLE audit_logs
                    ALTER COLUMN success TYPE BOOLEAN
                    USING CASE
                        WHEN success IS NULL THEN NULL
                        WHEN success = 0 THEN FALSE
                        ELSE TRUE
                    END;
                ALTER TABLE audit_logs ALTER COLUMN success SET DEFAULT TRUE;
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    # This is a schema-drift repair: the preceding PostgreSQL baseline already
    # declares BOOLEAN, so downgrading must not recreate the invalid INTEGER.
    return
