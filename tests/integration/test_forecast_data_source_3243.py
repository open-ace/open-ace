"""Integration tests for Issue #3243: Forecast data source consistency.

Tests that forecast API uses daily_messages for consistency with historical data API.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.modules.analytics.usage_analytics import UsageAnalytics


def _insert_daily_messages(tmp_db, date: str, tokens: int, tenant_id: int = 1) -> None:
    """Insert test data into daily_messages table."""
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, "qwen-code", "localhost", f"msg-{date}-1", "user", tokens // 2, tokens // 2, 0, tenant_id),
    )
    tmp_db.execute(
        """
        INSERT INTO daily_messages
        (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (date, "qwen-code", "localhost", f"msg-{date}-2", "assistant", tokens // 2, 0, tokens // 2, tenant_id),
    )


def test_forecast_uses_daily_messages_source(tmp_db):
    """Verify forecast API uses daily_messages data source (Issue #3243)."""
    # Prepare test data for 14 days (7 for training + 7 for backtest)
    today = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d")
    for i in range(14):
        date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        _insert_daily_messages(tmp_db, date, tokens=1000 + i * 100, tenant_id=1)

    # Call forecast API
    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1)

    # Verify forecast is available
    assert forecast["forecast_available"] is True
    assert forecast["method"] == "moving_average"

    # Verify daily_forecast tokens are based on daily_messages data
    # The forecast should use last 7 days average
    expected_avg = sum(1000 + i * 100 for i in range(7)) / 7  # 1300
    assert abs(forecast["daily_forecast"]["tokens"] - expected_avg) < 50  # Allow rounding


def test_forecast_tenant_isolation_with_daily_messages(tmp_db):
    """Verify forecast API tenant isolation uses daily_messages data (Issue #3243)."""
    # Insert data for tenant 1
    for i in range(14):
        date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        _insert_daily_messages(tmp_db, date, tokens=1000, tenant_id=1)

    # Insert data for tenant 2 (different token count)
    for i in range(14):
        date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        tmp_db.execute(
            """
            INSERT INTO daily_messages
            (date, tool_name, host_name, message_id, role, tokens_used, input_tokens, output_tokens, tenant_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (date, "qwen-code", "localhost", f"msg-tenant2-{date}", "assistant", 500, 250, 250, 2),
        )

    # Call forecast API for tenant 1
    analytics = UsageAnalytics(db=tmp_db)
    forecast_t1 = analytics.get_forecast(days=7, tenant_id=1)

    # Call forecast API for tenant 2
    forecast_t2 = analytics.get_forecast(days=7, tenant_id=2)

    # Verify tenant isolation
    assert forecast_t1["forecast_available"] is True
    assert forecast_t2["forecast_available"] is True
    assert forecast_t1["daily_forecast"]["tokens"] != forecast_t2["daily_forecast"]["tokens"]
    # Tenant 1 should have ~1000 tokens (from 2 messages per day, 500 each)
    # Tenant 2 should have ~500 tokens
    assert forecast_t1["daily_forecast"]["tokens"] > forecast_t2["daily_forecast"]["tokens"]


def test_forecast_returns_message_count_not_request_count(tmp_db):
    """Verify forecast API returns message_count, not request_count (Issue #3243)."""
    # Insert 3 messages per day (more messages than typical requests)
    for i in range(14):
        date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=i + 1)).strftime("%Y-%m-%d")
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
    forecast = analytics.get_forecast(days=7, tenant_id=1)

    # The requests field should be message_count (3 per day), not request_count
    # Since we're using daily_messages, this is expected behavior
    assert forecast["forecast_available"] is True
    # requests field reflects message count, which is 3 per day
    assert forecast["daily_forecast"]["requests"] == 3  # 3 messages per day average


def test_forecast_insufficient_data_returns_unavailable(tmp_db):
    """Verify forecast returns unavailable when daily_messages has insufficient data."""
    # Insert only 5 days of data (below minimum of 7)
    for i in range(5):
        date = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        _insert_daily_messages(tmp_db, date, tokens=1000, tenant_id=1)

    analytics = UsageAnalytics(db=tmp_db)
    forecast = analytics.get_forecast(days=7, tenant_id=1)

    # Should return unavailable due to insufficient data
    assert forecast["forecast_available"] is False
    assert "reason" in forecast