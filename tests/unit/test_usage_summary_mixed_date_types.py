"""Issue #3424: summary merges must tolerate PostgreSQL date objects.

daily_messages.date is a string column; ``CAST(created_at AS DATE)`` on
agent_sessions returns ``datetime.date`` on PostgreSQL. Merging the two compared
``date < str`` and raised TypeError, so:

- ``SummaryService.refresh_summary`` failed on every call, usage_summary stayed
  stale forever and each /api/hosts request re-ran the aggregation;
- ``/api/summary`` returned 500 whenever a tool had rows in both sources.
"""

from datetime import date, datetime
from unittest.mock import MagicMock

import pytest

from app.repositories.usage_repo import UsageRepository
from app.services.summary_service import SummaryService
from app.services.usage_service import UsageService
from app.utils.cache import get_cache
from app.utils.helpers import to_iso_date

pytestmark = [pytest.mark.regression, pytest.mark.issue(3424)]


def _agg(first, last, host="h1", tokens=10):
    return {
        "tool_name": "claude",
        "host_name": host,
        "days_count": 1,
        "total_tokens": tokens,
        "avg_tokens": tokens,
        "total_requests": 1,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "first_date": first,
        "last_date": last,
    }


def test_to_iso_date():
    assert to_iso_date(None) is None
    assert to_iso_date("") is None
    assert to_iso_date("2026-03-01") == "2026-03-01"
    assert to_iso_date(date(2026, 3, 1)) == "2026-03-01"
    assert to_iso_date(datetime(2026, 3, 1, 12, 30)) == "2026-03-01"
    # SQLite CAST(... AS DATE) returns a bare year; it is not a date
    assert to_iso_date(2026) is None


def test_summary_merge_accepts_str_and_date():
    service = SummaryService(db=MagicMock())
    merged = service._merge_aggregates(
        [
            _agg("2026-03-10", "2026-05-01"),  # daily_messages (str)
            _agg(date(2026, 3, 1), date(2026, 8, 20)),  # agent_sessions (date)
        ]
    )
    assert len(merged) == 1
    assert merged[0]["first_date"] == "2026-03-01"
    assert merged[0]["last_date"] == "2026-08-20"
    assert merged[0]["total_tokens"] == 20


def test_usage_summary_merges_session_dates():
    get_cache().clear()
    db = MagicMock()
    db.fetch_all.side_effect = [
        # get_summary_by_tool (daily_messages)
        [
            {
                "tool_name": "claude",
                "days_count": 2,
                "total_tokens": 100,
                "avg_tokens": 50,
                "total_requests": 2,
                "total_input_tokens": 60,
                "total_output_tokens": 40,
                "first_date": "2026-03-10",
                "last_date": "2026-05-01",
            }
        ],
        # get_session_summary_by_tool (agent_sessions, PostgreSQL date objects)
        [
            {
                "tool_name": "claude",
                "days_count": 3,
                "total_tokens": 30,
                "total_requests": 3,
                "total_input_tokens": 20,
                "total_output_tokens": 10,
                "first_date": date(2026, 3, 1),
                "last_date": date(2026, 8, 20),
            }
        ],
    ]
    repo = UsageRepository(db=db)
    summary = UsageService(usage_repo=repo).get_usage_summary(
        start_date="2026-01-01", end_date="2026-09-30"
    )

    assert summary["claude"]["first_date"] == "2026-03-01"
    assert summary["claude"]["last_date"] == "2026-08-20"
    assert summary["claude"]["total_tokens"] == 130
