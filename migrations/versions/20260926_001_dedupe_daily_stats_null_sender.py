"""Drop duplicate NULL-sender rows accumulated in daily_stats.

daily_stats is keyed on (date, tool_name, host_name, sender_name). NULLs are
distinct in that unique constraint (it is not NULLS NOT DISTINCT; SQLite
behaves the same), so the refresh
upsert never matched a NULL-sender group and appended another copy on every
refresh. Long-lived installations accumulated hundreds of thousands of copies,
which doubled dashboard trend totals and slowed every refresh (#3424).

The refresh now replaces NULL-sender rows in place; this keeps only the newest
copy per (date, tool_name, host_name) so existing data is correct right after
upgrade instead of after the next full refresh. Idempotent: a clean table
deletes nothing. Data-only, no schema change.
"""

from alembic import op

revision = "20260926_001_dedupe_daily_stats_null_sender"
down_revision = "20260921_001_external_identity_nonces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    row_id = "ctid" if op.get_bind().dialect.name == "postgresql" else "rowid"
    op.execute(f"""
        DELETE FROM daily_stats
        WHERE {row_id} IN (
            SELECT row_ref FROM (
                SELECT
                    {row_id} AS row_ref,
                    ROW_NUMBER() OVER (
                        PARTITION BY date, tool_name, host_name
                        ORDER BY updated_at DESC, {row_id} DESC
                    ) AS rn
                FROM daily_stats
                WHERE sender_name IS NULL
            ) ranked
            WHERE rn > 1
        )
        """)


def downgrade() -> None:
    # The deleted rows were redundant copies; there is nothing to restore.
    return
