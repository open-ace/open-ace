"""Integration tests for Issue #3243: Forecast data source consistency.

Tests that forecast API uses daily_messages for consistency with historical data API.

A companion ``_pg.py`` variant (test_forecast_data_source_3243_pg.py) runs the
core assertions on PostgreSQL — the deployment class where #3243 was observed.

Determinism: every test computes ONE ``business_date`` string and derives both
its seed dates and the ``get_forecast(business_date=...)`` call from it, so a
UTC-midnight rollover between seeding and forecasting cannot shift the window
(the round-1 review flagged the previous ``datetime.now()``-per-call form).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.analytics.usage_analytics import UsageAnalytics


def _business_date() -> str:
    """Fixed business date for one test invocation (current UTC date)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(business_date: str, n: int) -> str:
    """Date string n completed days before business_date."""
    dt = datetime.strptime(business_date, "%Y-%m-%d")
    return (dt - timedelta(days=n)).strftime("%Y-%m-%d")


def _insert_daily_messages(db, date: str, tokens: int, tenant_id: int = 1) -> None:
    """Insert test data into daily_messages table (user + assistant rows)."""
    db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date,
            "qwen-code",
            "localhost",
            f"msg-t{tenant_id}-{date}-1",
            "user",
            tokens // 2,
            tokens // 2,
            0,
            tenant_id,
        ),
    )
    db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            date,
            "qwen-code",
            "localhost",
            f"msg-t{tenant_id}-{date}-2",
            "assistant",
            tokens // 2,
            0,
            tokens // 2,
            tenant_id,
        ),
    )


def _insert_daily_usage(db, date: str, tokens: int, requests: int, tenant_id: int = 1) -> None:
    """Insert test data into daily_usage (the pre-#3243 forecast source)."""
    db.execute(
        """
        INSERT INTO daily_usage
        (date, tool_name, host_name, tokens_used, request_count, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (date, "qwen-code", "localhost", tokens, requests, tenant_id),
    )


def test_forecast_uses_daily_messages_source(tmp_db):
    """Verify forecast API uses daily_messages data source (Issue #3243)."""
    biz = _business_date()
    # Prepare test data for 14 days (7 for training + 7 for backtest)
    for i in range(14):
        _insert_daily_messages(tmp_db, _days_ago(biz, i + 1), tokens=1000 + i * 100, tenant_id=1)

    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is True
    assert forecast["method"] == "moving_average"

    # The forecast should use last 7 days average of daily_messages data
    # (window = days-ago 1..7, seeded with tokens 1000..1600)
    expected_avg = sum(1000 + 100 * i for i in range(7)) / 7  # 1300
    assert abs(forecast["daily_forecast"]["tokens"] - expected_avg) < 50


def test_forecast_ignores_daily_usage_only_data(tmp_db):
    """Reverse pin: daily_usage alone must NOT feed the forecast any more.

    This is the regression that silently bit PR #3316 round 1 — #3244's
    refactor moved get_forecast onto new methods and the original switch
    landed on a dead one. Seeding ONLY daily_usage (the pre-#3243 source)
    must leave the forecast unavailable.
    """
    biz = _business_date()
    for i in range(14):
        _insert_daily_usage(
            tmp_db, _days_ago(biz, i + 1), tokens=133_000_000, requests=42, tenant_id=1
        )

    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is False
    assert forecast["reason"] == "No historical data available"


def test_forecast_tenant_isolation_with_daily_messages(tmp_db):
    """Verify forecast API tenant isolation uses daily_messages data (Issue #3243)."""
    biz = _business_date()
    for i in range(14):
        _insert_daily_messages(tmp_db, _days_ago(biz, i + 1), tokens=1000, tenant_id=1)

    # Tenant 2 data (different token count)
    for i in range(14):
        date = _days_ago(biz, i + 1)
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date, "qwen-code", "localhost", f"msg-tenant2-{date}", "assistant", 500, 250, 250, 2),
        )

    analytics = UsageAnalytics(db=tmp_db)
    forecast_t1 = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)
    forecast_t2 = analytics.get_forecast(days=7, tenant_id=2, business_date=biz)

    assert forecast_t1["forecast_available"] is True
    assert forecast_t2["forecast_available"] is True
    assert forecast_t1["daily_forecast"]["tokens"] != forecast_t2["daily_forecast"]["tokens"]
    # Tenant 1 should have ~1000 tokens (2 messages, 500 each); tenant 2 ~500
    assert forecast_t1["daily_forecast"]["tokens"] > forecast_t2["daily_forecast"]["tokens"]


def test_forecast_returns_message_count_not_request_count(tmp_db):
    """Verify forecast requests mirrors the history API's per-message count."""
    biz = _business_date()
    for i in range(14):
        date = _days_ago(biz, i + 1)
        for j in range(3):
            tmp_db.execute(
                """
                INSERT INTO daily_messages
                (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (date, "qwen-code", "localhost", f"msg-{date}-{j}", "assistant", 100, 50, 50, 1),
            )

    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is True
    # requests reflects message count (3 per day), not daily_usage.request_count
    assert forecast["daily_forecast"]["requests"] == 3


def test_forecast_partial_data_degrades_quality(tmp_db):
    """#3244 semantics: partial data degrades quality instead of disabling forecast.

    4 days of data with a 2-day gap inside the first-activity-bounded window
    (Issue #3244 bounds window start to first activity date) → missing_days
    reaches the degraded threshold → forecast available with quality "fair".
    """
    biz = _business_date()
    for i in (0, 1, 4, 5):  # days-ago 1,2,5,6 — days-ago 3,4 stay empty
        _insert_daily_messages(tmp_db, _days_ago(biz, i + 1), tokens=1000, tenant_id=1)

    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1, business_date=biz)

    assert forecast["forecast_available"] is True
    assert forecast["quality_level"] == "fair"
    assert forecast["quality_metrics"]["sample_days"] == 4


def test_forecast_no_data_returns_unavailable(tmp_db):
    """With no daily_messages data at all the forecast is unavailable."""
    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1, business_date=_business_date())

    assert forecast["forecast_available"] is False
    assert forecast["reason"] == "No historical data available"
