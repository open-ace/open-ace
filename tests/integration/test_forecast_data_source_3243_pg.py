"""Forecast data-source consistency on PostgreSQL (#3243).

Companion to test_forecast_data_source_3243.py; the ``_pg.py`` suffix puts it
in the postgres CI lane. Issue #3243's acceptance criteria explicitly require
PostgreSQL coverage — the 686x history/forecast discrepancy was observed on a
multi-user PostgreSQL deployment, and the forecast queries now aggregate
daily_messages directly (SUM/COUNT over the raw table with a tenant filter).
"""

import pytest

from app.modules.analytics.usage_analytics import UsageAnalytics
from tests.integration.test_forecast_data_source_3243 import (
    _business_date,
    _days_ago,
    _insert_daily_messages,
    _insert_daily_usage,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.postgres,
    pytest.mark.regression,
    pytest.mark.issue(3243),
]


def test_forecast_reads_daily_messages_on_postgres(pg_db):
    """The forecast series on PostgreSQL comes from daily_messages."""
    biz = _business_date()
    for i in range(14):
        _insert_daily_messages(pg_db, _days_ago(biz, i + 1), tokens=1000 + 100 * i, tenant_id=1)

    forecast = UsageAnalytics(db=pg_db).get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is True
    expected_avg = sum(1000 + 100 * i for i in range(7)) / 7  # 1300
    assert abs(forecast["daily_forecast"]["tokens"] - expected_avg) < 50


def test_forecast_tenant_isolation_on_postgres(pg_db):
    """Tenant filtering on PostgreSQL uses the same daily_messages predicate."""
    biz = _business_date()
    for i in range(14):
        _insert_daily_messages(pg_db, _days_ago(biz, i + 1), tokens=1000, tenant_id=1)
        _insert_daily_messages(pg_db, _days_ago(biz, i + 1), tokens=500, tenant_id=2)

    analytics = UsageAnalytics(db=pg_db)
    t1 = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)
    t2 = analytics.get_forecast(days=7, tenant_id=2, business_date=biz)

    assert t1["forecast_available"] is True
    assert t2["forecast_available"] is True
    assert abs(t1["daily_forecast"]["tokens"] - 1000) < 50
    assert abs(t2["daily_forecast"]["tokens"] - 500) < 50


def test_forecast_ignores_daily_usage_only_data_on_postgres(pg_db):
    """Reverse pin on PostgreSQL: daily_usage alone must not feed the forecast."""
    biz = _business_date()
    for i in range(14):
        _insert_daily_usage(
            pg_db, _days_ago(biz, i + 1), tokens=133_000_000, requests=42, tenant_id=1
        )

    forecast = UsageAnalytics(db=pg_db).get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is False
    assert forecast["reason"] == "No historical data available"
