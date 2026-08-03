"""PG-only regression: alert INSERTs must write a boolean to the read column (#2260).

Two live code paths inserted an integer (0 / ``1 if x else 0``) into the
boolean ``alerts.read`` column. PostgreSQL has no int->boolean assignment cast,
so both failed with ``column "read" is of type boolean but expression is of
type integer``:

  * ``AlertTransactionManager._create_alert`` (the quota-alert path) — 3 retries,
    then dead-lettered; no alert ever persisted (the active prod failure).
  * ``AlertNotifier._save_alert`` (reached via ``create_alert`` / the ``/alerts``
    HTTP endpoint at routes/alerts.py:262).

SQLite's INTEGER type affinity accepts 0/1, so CI's SQLite matrix stayed green —
a PG-only regression. These tests must run on a live PostgreSQL to catch it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import app.modules.governance.alert_notifier as alert_notifier_mod
import app.repositories.database as db_mod

# Marks every test in this module as requiring a live PostgreSQL server.
pytestmark = pytest.mark.postgres

from app.modules.governance.alert_notifier import Alert, AlertNotifier
from app.modules.governance.alert_transaction_manager import AlertTransactionManager, QuotaAlertData


def _seed_user(db, user_id: int = 1) -> None:
    """Insert the users row that quota_alerts.user_id references (FK)."""
    db.execute(
        "INSERT INTO users (id, username, email, password_hash, role) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, f"user{user_id}", f"user{user_id}@test.com", "hash", "user"),
    )


def test_create_quota_alert_persists_with_read_false(pg_db):
    """AlertTransactionManager._create_alert: the alerts.read boolean column
    must accept the insert on PostgreSQL.

    Pre-fix the int 0 literal caused DatatypeMismatch; the transaction rolled
    back (no alerts row, no quota_alerts row) after 3 retries.
    """
    _seed_user(pg_db)
    manager = AlertTransactionManager(db=pg_db)
    alert_data = QuotaAlertData(
        user_id=1,
        username="user1",
        quota_type="tokens",
        usage_percent=95.0,
        current_usage=950,
        quota_limit=1000,
        threshold=90.0,
        original_alert_type="warning",
    )

    success, alert_id = manager.create_quota_alert_transactional(alert_data)

    assert success is True
    assert alert_id is not None
    row = pg_db.fetch_one("SELECT * FROM alerts WHERE alert_id = %s", (alert_id,))
    assert row is not None
    # Boolean column must come back as a Python bool, not an int — and the row
    # must exist at all (the pre-fix rollback left nothing).
    assert row["read"] is False


def test_save_alert_persists_with_read_false(pg_db):
    """AlertNotifier._save_alert (via create_alert / the /alerts endpoint) shares
    the int->boolean bug. Route it to the test DB by delegating the module-level
    is_postgresql/get_database_url names — these are bound into alert_notifier
    via ``from ... import``, so the pg_db fixture's patch on the database module
    doesn't reach them; delegate to the fixture's already-patched names instead.
    """
    _seed_user(pg_db)
    notifier = AlertNotifier()
    alert = Alert(
        alert_id="alt-save-1",
        alert_type="system",
        severity="warning",
        title="save-alert-test",
        message="m",
        user_id=1,
        username="user1",
    )

    with (
        patch.object(alert_notifier_mod, "is_postgresql", db_mod.is_postgresql),
        patch.object(alert_notifier_mod, "get_database_url", db_mod.get_database_url),
    ):
        notifier._save_alert(alert)

    row = pg_db.fetch_one("SELECT * FROM alerts WHERE alert_id = %s", ("alt-save-1",))
    assert row is not None
    assert row["read"] is False
