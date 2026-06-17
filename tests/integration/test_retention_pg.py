"""Integration tests for DataRetentionManager against real PostgreSQL.

Guards the regression fixed in issue #860: the retention persistence SQL used
SQLite-style ``?`` placeholders without ``adapt_sql()``, so on PostgreSQL the
INSERT into ``retention_history`` raised a syntax error that was silently
swallowed (only ``logger.error``) -> cleanup history was never saved and the
cleanup result was never surfaced. The bug only manifests on PostgreSQL
(SQLite accepts ``?``), so these tests run against the ``pg_db`` fixture (a
real, isolated PostgreSQL database) and precisely reproduce the symptom:
before the fix these assertions fail (history stays empty); after the fix they
pass.

The source-table DELETE/ANONYMIZE placeholder sites are additionally covered
by ``tests/unit/test_retention.py`` (which spies on the driver SQL without
needing a server). This file focuses on the headline bug -- history
persistence.

Run (requires a reachable PostgreSQL via ``PG_TEST_URL``):
    pytest tests/integration/test_retention_pg.py -v
"""

from datetime import datetime

from app.modules.compliance.retention import DataRetentionManager, RetentionReport


class TestRetentionHistoryPersistencePostgres:
    """Verify retention report persistence works on PostgreSQL (issue #860)."""

    def _manager(self, pg_db):
        manager = DataRetentionManager(db=pg_db)
        # Idempotent CREATE TABLE IF NOT EXISTS; guarantees retention_history
        # exists regardless of fixture DDL ordering. Uses no placeholders.
        manager._ensure_tables()
        return manager

    def test_save_report_persists_and_reads_back(self, pg_db):
        """A saved cleanup report must round-trip via get_retention_history on PG.

        Before the fix: INSERT used '?' placeholders -> psycopg2 syntax error
        -> swallowed by _save_report's except -> history stayed empty.
        """
        manager = self._manager(pg_db)
        report = RetentionReport(
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            rules_applied=[
                {
                    "data_type": "messages",
                    "action": "delete",
                    "cutoff": "2025-01-01T00:00:00",
                    "records_affected": 5,
                }
            ],
            records_deleted=5,
            records_archived=2,
            records_anonymized=1,
        )

        manager._save_report(report)

        history = manager.get_retention_history()
        assert len(history) >= 1
        latest = history[0]
        assert latest["records_deleted"] == 5
        assert latest["status"] == "success"
        assert latest["cleanup_type"] == "scheduled"

    def test_run_cleanup_persists_history_on_postgres(self, pg_db):
        """run_cleanup(dry_run=False) must persist a history row on PostgreSQL.

        Default rules are cleared to isolate the persistence path from
        per-table schema differences in the test fixture; the goal is to prove
        the report is saved end-to-end on PG (the #860 symptom).
        """
        manager = self._manager(pg_db)
        manager.rules = {}  # skip source-table deletes; isolate history persistence

        report = manager.run_cleanup(dry_run=False)

        assert report.records_deleted == 0  # no rules -> nothing deleted
        history = manager.get_retention_history()
        assert len(history) >= 1
        assert history[0]["status"] == "success"
