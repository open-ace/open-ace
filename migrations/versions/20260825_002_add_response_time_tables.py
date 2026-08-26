"""
Migration: Add response time tracking tables for Issue #3080

Creates two tables:
1. request_performance: Raw request performance events
2. response_time_stats: Pre-aggregated response time statistics
"""

from alembic import op

# revision identifiers
revision = "20260825_002"
down_revision = "20260825_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create response time tracking tables."""
    conn = op.get_bind()

    if conn.dialect.name == "postgresql":
        # PostgreSQL version
        op.execute("""
            CREATE TABLE IF NOT EXISTS request_performance (
                id SERIAL PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                session_id TEXT,
                conversation_id TEXT,
                tenant_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                user_id INTEGER,

                -- Timestamps (monotonic clock)
                started_at TIMESTAMP NOT NULL,
                first_response_at TIMESTAMP,
                completed_at TIMESTAMP,

                -- Computed fields (milliseconds)
                ttft_ms INTEGER,
                tool_call_duration_ms INTEGER DEFAULT 0,
                total_duration_ms INTEGER,

                -- Request metadata
                status TEXT NOT NULL DEFAULT 'success',
                sample_type TEXT DEFAULT 'streaming',
                model TEXT,

                -- Tool call statistics
                tool_call_count INTEGER DEFAULT 0,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_date
            ON request_performance(started_at)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_tenant
            ON request_performance(tenant_id)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_tool
            ON request_performance(tool_name, started_at)
        """)

        op.execute("""
            CREATE TABLE IF NOT EXISTS response_time_stats (
                id SERIAL PRIMARY KEY,
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                tenant_id INTEGER NOT NULL,

                -- Statistical metrics
                avg_ms REAL,
                p50_ms INTEGER,
                p95_ms INTEGER,
                min_ms INTEGER,
                max_ms INTEGER,

                -- Tool call statistics
                tool_call_avg_ms REAL,
                tool_call_ratio REAL,

                -- Sample statistics
                sample_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,

                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE (date, tool_name, host_name, tenant_id)
            )
        """)

        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_time_stats_date
            ON response_time_stats(date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_time_stats_tenant
            ON response_time_stats(tenant_id, date)
        """)
    else:
        # SQLite version
        op.execute("""
            CREATE TABLE IF NOT EXISTS request_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE,
                session_id TEXT,
                conversation_id TEXT,
                tenant_id INTEGER NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                user_id INTEGER,

                -- Timestamps (monotonic clock)
                started_at TIMESTAMP NOT NULL,
                first_response_at TIMESTAMP,
                completed_at TIMESTAMP,

                -- Computed fields (milliseconds)
                ttft_ms INTEGER,
                tool_call_duration_ms INTEGER DEFAULT 0,
                total_duration_ms INTEGER,

                -- Request metadata
                status TEXT NOT NULL DEFAULT 'success',
                sample_type TEXT DEFAULT 'streaming',
                model TEXT,

                -- Tool call statistics
                tool_call_count INTEGER DEFAULT 0,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_date
            ON request_performance(started_at)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_tenant
            ON request_performance(tenant_id)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_request_performance_tool
            ON request_performance(tool_name, started_at)
        """)

        op.execute("""
            CREATE TABLE IF NOT EXISTS response_time_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                host_name TEXT DEFAULT 'localhost',
                tenant_id INTEGER NOT NULL,

                -- Statistical metrics
                avg_ms REAL,
                p50_ms INTEGER,
                p95_ms INTEGER,
                min_ms INTEGER,
                max_ms INTEGER,

                -- Tool call statistics
                tool_call_avg_ms REAL,
                tool_call_ratio REAL,

                -- Sample statistics
                sample_count INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,

                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                UNIQUE (date, tool_name, host_name, tenant_id)
            )
        """)

        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_time_stats_date
            ON response_time_stats(date)
        """)
        op.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_time_stats_tenant
            ON response_time_stats(tenant_id, date)
        """)


def downgrade() -> None:
    """Drop response time tracking tables."""
    op.execute("DROP INDEX IF EXISTS idx_response_time_stats_tenant")
    op.execute("DROP INDEX IF EXISTS idx_response_time_stats_date")
    op.execute("DROP TABLE IF EXISTS response_time_stats")

    op.execute("DROP INDEX IF EXISTS idx_request_performance_tool")
    op.execute("DROP INDEX IF EXISTS idx_request_performance_tenant")
    op.execute("DROP INDEX IF EXISTS idx_request_performance_date")
    op.execute("DROP TABLE IF EXISTS request_performance")
