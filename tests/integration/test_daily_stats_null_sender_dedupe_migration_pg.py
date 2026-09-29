"""Migration 20260926_001 on PostgreSQL (ctid path) (#3424).

The SQLite (rowid) case and the shared fixtures live in
test_daily_stats_null_sender_dedupe_migration.py; this file carries the
``_pg.py`` suffix so the postgres CI lane selects it.
"""

import pytest
import sqlalchemy as sa

from tests.integration.test_daily_stats_null_sender_dedupe_migration import (
    _EXPECTED,
    _remaining,
    _seed,
    _upgrade,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.regression,
    pytest.mark.issue(3424),
]


def test_dedupe_keeps_newest_null_sender_row_postgres(pg_db):
    engine = sa.create_engine(pg_db.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM daily_stats"))
            _seed(conn)
            _upgrade(conn)
            assert _remaining(conn) == _EXPECTED
            # Idempotent: a second run deletes nothing.
            _upgrade(conn)
            assert _remaining(conn) == _EXPECTED
    finally:
        engine.dispose()
