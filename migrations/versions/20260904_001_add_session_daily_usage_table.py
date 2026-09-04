"""
Migration: Add session_daily_usage table for Issue #3338

Creates the session_daily_usage table that was missing from PR #3320.
This table stores per-session, per-day incremental usage data for accurate
quota tracking in long-running WebUI sessions.

Fixes #3338
"""

from alembic import op

# revision identifiers
revision = "20260904_001"
down_revision = "20260827_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create session_daily_usage table."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # PostgreSQL version
        op.execute("""
            CREATE TABLE IF NOT EXISTS session_daily_usage (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id INTEGER,
                tenant_id INTEGER,
                date TEXT NOT NULL,

                -- Token metrics
                tokens INTEGER DEFAULT 0 NOT NULL,
                requests INTEGER DEFAULT 0 NOT NULL,
                input_tokens INTEGER DEFAULT 0 NOT NULL,
                output_tokens INTEGER DEFAULT 0 NOT NULL,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,

                -- Timestamps
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

                -- Unique constraint: one record per session per day
                CONSTRAINT uq_session_daily_usage_session_date UNIQUE (session_id, date)
            )
        """)

        # Indexes for common query patterns
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_user_date
            ON session_daily_usage(user_id, date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_date
            ON session_daily_usage(date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_tenant
            ON session_daily_usage(tenant_id)
        """)
    else:
        # SQLite version
        op.execute("""
            CREATE TABLE IF NOT EXISTS session_daily_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id INTEGER,
                tenant_id INTEGER,
                date TEXT NOT NULL,

                -- Token metrics
                tokens INTEGER DEFAULT 0 NOT NULL,
                requests INTEGER DEFAULT 0 NOT NULL,
                input_tokens INTEGER DEFAULT 0 NOT NULL,
                output_tokens INTEGER DEFAULT 0 NOT NULL,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,

                -- Timestamps
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

                -- Unique constraint: one record per session per day
                CONSTRAINT uq_session_daily_usage_session_date UNIQUE (session_id, date)
            )
        """)

        # Indexes for common query patterns
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_user_date
            ON session_daily_usage(user_id, date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_date
            ON session_daily_usage(date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_session_daily_usage_tenant
            ON session_daily_usage(tenant_id)
        """)


def downgrade() -> None:
    """Drop session_daily_usage table."""
    # Drop indexes
    op.execute("DROP INDEX IF EXISTS idx_session_daily_usage_tenant")
    op.execute("DROP INDEX IF EXISTS idx_session_daily_usage_date")
    op.execute("DROP INDEX IF EXISTS idx_session_daily_usage_user_date")

    # Drop table
    op.execute("DROP TABLE IF EXISTS session_daily_usage")
