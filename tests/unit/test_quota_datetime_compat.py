"""Regression tests for PostgreSQL datetime compatibility in quota_manager.

PostgreSQL/psycopg2 returns ``datetime`` objects for TIMESTAMP columns, whereas
SQLite returns ISO 8601 strings. The quota alert row-conversion code previously
called ``datetime.fromisoformat(row["created_at"])`` unconditionally, which
raises ``TypeError`` when handed a ``datetime`` instance. This caused
``QuotaManager.check_quota`` to throw, which in turn paused autonomous
workflows with a misleading "Quota check unavailable" reason.

These tests verify that alert row conversion handles both ``datetime`` objects
(PostgreSQL) and strings (SQLite) without raising.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.modules.governance.quota_manager import QuotaManager


def _build_manager_with_rows(rows):
    """Build a QuotaManager whose db.fetch_all returns *rows*."""
    mgr = QuotaManager.__new__(QuotaManager)
    mgr.db = MagicMock()
    mgr.db.fetch_all.return_value = rows
    return mgr


def _sample_row(created_at, acknowledged_at=None):
    """Build a dict row mimicking quota_alerts columns."""
    return {
        "id": 1,
        "user_id": 89,
        "alert_type": "warning",
        "quota_type": "tokens",
        "period": "daily",
        "threshold": 80.0,
        "current_usage": 90,
        "quota_limit": 100,
        "percentage": 90.0,
        "message": "Approaching limit",
        "created_at": created_at,
        "acknowledged": 0,
        "acknowledged_at": acknowledged_at,
        "acknowledged_by": None,
    }


def test_get_recent_alerts_handles_postgres_datetime_objects():
    """PostgreSQL returns datetime objects; conversion must not raise."""
    created = datetime(2026, 7, 31, 11, 19, 33)
    acknowledged = datetime(2026, 7, 31, 12, 0, 0)
    mgr = _build_manager_with_rows([_sample_row(created, acknowledged)])

    alerts = mgr._get_recent_alerts(user_id=89)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.created_at == created
    assert alert.acknowledged_at == acknowledged


def test_get_recent_alerts_handles_sqlite_strings():
    """SQLite returns ISO strings; conversion must still work."""
    created = "2026-07-31T11:19:33"
    acknowledged = "2026-07-31T12:00:00"
    mgr = _build_manager_with_rows([_sample_row(created, acknowledged)])

    alerts = mgr._get_recent_alerts(user_id=89)

    assert len(alerts) == 1
    assert alerts[0].created_at == datetime(2026, 7, 31, 11, 19, 33)
    assert alerts[0].acknowledged_at == datetime(2026, 7, 31, 12, 0, 0)


def test_get_recent_alerts_handles_null_acknowledged_at():
    """acknowledged_at is nullable; should resolve to None."""
    created = datetime(2026, 7, 31, 11, 19, 33)
    mgr = _build_manager_with_rows([_sample_row(created, acknowledged_at=None)])

    alerts = mgr._get_recent_alerts(user_id=89)

    assert alerts[0].acknowledged_at is None


def test_get_recent_alerts_falls_back_when_created_at_missing():
    """When created_at is NULL, a sensible default is used."""
    mgr = _build_manager_with_rows([_sample_row(created_at=None)])

    alerts = mgr._get_recent_alerts(user_id=89)

    assert len(alerts) == 1
    assert alerts[0].created_at is not None


def test_get_all_alerts_handles_postgres_datetime_objects():
    """get_all_alerts shares the same conversion path."""
    created = datetime(2026, 7, 31, 11, 19, 33, tzinfo=timezone.utc)
    mgr = _build_manager_with_rows([_sample_row(created)])

    alerts = mgr.get_all_alerts()

    assert len(alerts) == 1
    assert alerts[0].created_at == created
