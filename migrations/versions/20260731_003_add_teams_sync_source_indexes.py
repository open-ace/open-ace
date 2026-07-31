"""Add indexes for teams sync_source performance optimization.

Issue #2174 F1/F5: Optimize organization sync full table scan.

Revision ID: 20260731_003_add_teams_sync_source_indexes
Revises: 20260731_002_add_ci_repair_transient_retries
Create Date: 2026-07-31

"""
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '20260731_003_add_teams_sync_source_indexes'
down_revision: str | None = '20260731_002_add_ci_repair_transient_retries'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add partial indexes for organization sync performance.

    These indexes optimize the _load_synced_teams queries in
    feishu_org_sync.py and dingtalk_org_sync.py by filtering
    at the database level instead of in Python.

    Uses CONCURRENTLY to avoid table locks on large datasets.
    """
    # PostgreSQL-specific: Use CONCURRENTLY to avoid blocking
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        # Feishu partial index
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_teams_feishu_sync
            ON teams ((settings->>'sync_source'), (settings->>'feishu_department_id'))
            WHERE settings->>'sync_source' = 'feishu'
        """)

        # DingTalk partial index
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_teams_dingtalk_sync
            ON teams ((settings->>'sync_source'), (settings->>'dingtalk_department_id'))
            WHERE settings->>'sync_source' = 'dingtalk'
        """)
    else:
        # SQLite fallback (no partial indexes support in older versions)
        # Create regular indexes on the JSON extraction
        # Note: SQLite may not use these effectively without partial index support
        try:
            op.execute("""
                CREATE INDEX IF NOT EXISTS idx_teams_sync_source
                ON teams ((settings->>'sync_source'))
            """)
        except Exception:
            # SQLite may not support JSON expression indexes
            pass


def downgrade() -> None:
    """Remove organization sync performance indexes."""
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == 'postgresql':
        op.execute("DROP INDEX IF EXISTS idx_teams_feishu_sync")
        op.execute("DROP INDEX IF EXISTS idx_teams_dingtalk_sync")
    else:
        try:
            op.execute("DROP INDEX IF EXISTS idx_teams_sync_source")
        except Exception:
            pass