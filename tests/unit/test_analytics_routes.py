"""Unit tests for analytics routes date range parsing."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.routes.analytics import parse_date_range, validate_forecast_days

pytestmark = [pytest.mark.regression, pytest.mark.issue(3253)]


class TestValidateForecastDays:
    """Test validate_forecast_days function."""

    def test_none_returns_default(self):
        """Test that None returns default value 7."""
        days, error = validate_forecast_days(None)
        assert days == 7
        assert error is None

    def test_valid_integer_1(self):
        """Test valid integer 1."""
        days, error = validate_forecast_days("1")
        assert days == 1
        assert error is None

    def test_valid_integer_7(self):
        """Test valid integer 7."""
        days, error = validate_forecast_days("7")
        assert days == 7
        assert error is None

    def test_valid_integer_90(self):
        """Test valid integer 90."""
        days, error = validate_forecast_days("90")
        assert days == 90
        assert error is None

    def test_invalid_zero(self):
        """Test that days=0 returns error."""
        days, error = validate_forecast_days("0")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert "1 and 90" in error["message"]

    def test_invalid_negative(self):
        """Test that negative days returns error."""
        days, error = validate_forecast_days("-1")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["received"] == -1

    def test_invalid_exceeds_max(self):
        """Test that days > 90 returns error."""
        days, error = validate_forecast_days("91")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["received"] == 91

    def test_invalid_non_integer(self):
        """Test that non-integer returns error."""
        days, error = validate_forecast_days("abc")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["received"] == "abc"

    def test_invalid_float(self):
        """Test that float returns error."""
        days, error = validate_forecast_days("7.5")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["received"] == "7.5"

    def test_large_integer(self):
        """Test large integer returns error."""
        days, error = validate_forecast_days("999999999")
        assert error is not None
        assert error["error"] == "invalid_parameter"


class TestParseDateRange:
    """Test parse_date_range function (strict validation, issue #3253)."""

    def _create_app_with_request_context(self, query_string=""):
        """Create a Flask app with request context for testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/test")
        def _parse_route():
            # Not a test: a Flask view registered only so this helper's
            # request context can exercise parse_date_range() directly.
            return parse_date_range()

        return app

    def _parse(self, query_string):
        """Run parse_date_range() inside a request context."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context(f"/test?{query_string}"):
                return parse_date_range()

    # --- valid requests ---

    def test_default_values(self):
        """Test default date range when no parameters provided."""
        start_date, end_date, days, error = self._parse("")
        assert error is None
        # Default: end_date=today, days=30
        assert days == 30
        assert end_date is not None
        assert start_date is not None

    def test_explicit_start_date_and_end_date(self):
        """Test explicit start_date and end_date parameters."""
        start_date, end_date, days, error = self._parse("start_date=2026-01-01&end_date=2026-01-31")
        assert error is None
        assert start_date == "2026-01-01"
        assert end_date == "2026-01-31"
        assert days == 30  # days is returned but not used when start_date is explicit

    def test_only_start_date_uses_default_end_date(self):
        """Test that providing only start_date uses default end_date (today)."""
        start_date, end_date, days, error = self._parse("start_date=2026-01-01")
        assert error is None
        assert start_date == "2026-01-01"
        # end_date defaults to today
        assert end_date is not None

    def test_only_end_date_uses_days_to_calculate_start(self):
        """Test that providing end_date + days calculates start_date from end_date."""
        start_date, end_date, days, error = self._parse("end_date=2026-01-31&days=7")
        assert error is None
        # Exactly 7 calendar days ending at end_date (double-inclusive SQL
        # bounds): start_date = end_date - (days - 1) = 2026-01-25
        assert start_date == "2026-01-25"
        assert end_date == "2026-01-31"
        assert days == 7

    def test_days_7(self):
        """Test 7-day range."""
        start_date, end_date, days, error = self._parse("days=7")
        assert error is None
        assert days == 7

    def test_days_90(self):
        """Test 90-day range."""
        start_date, end_date, days, error = self._parse("days=90")
        assert error is None
        assert days == 90

    def test_days_boundary_1_valid(self):
        """Test that days=1 (lower boundary) is accepted."""
        start_date, end_date, days, error = self._parse("end_date=2026-01-31&days=1")
        assert error is None
        assert days == 1
        # Exactly one calendar day: the start IS the end.
        assert start_date == "2026-01-31"

    def test_days_boundary_365_valid(self):
        """Test that days=365 (upper boundary) is accepted."""
        start_date, end_date, days, error = self._parse("days=365")
        assert error is None
        assert days == 365

    def test_historical_end_date_with_days(self):
        """Test historical end_date with days calculates correct start_date."""
        start_date, end_date, days, error = self._parse("end_date=2025-12-01&days=30")
        assert error is None
        # Exactly 30 calendar days ending at end_date:
        # start_date = 2025-12-01 - (30 - 1) days = 2025-11-02
        assert start_date == "2025-11-02"
        assert end_date == "2025-12-01"
        assert days == 30

    @pytest.mark.parametrize(
        "days,expected_start",
        [
            (1, "2026-09-29"),
            (7, "2026-09-23"),
            (30, "2026-08-31"),
            (90, "2026-07-02"),
            (365, "2025-09-30"),
        ],
    )
    def test_days_only_covers_exactly_n_calendar_days(self, days, expected_start):
        """Issue #3254: days-only quick ranges must cover exactly N calendar days.

        The SQL filters use double-inclusive bounds (date >= ? AND date <= ?),
        so a days-only request derived with end_date - days spanned N + 1 days.
        """
        start_date, end_date, returned_days, error = self._parse(f"end_date=2026-09-29&days={days}")
        assert error is None
        assert start_date == expected_start
        assert end_date == "2026-09-29"
        assert returned_days == days
        # Inclusive span start..end is exactly N calendar days.
        span = (
            datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")
        ).days + 1
        assert span == days

    def test_historical_range_with_explicit_dates(self):
        """Test explicit historical date range."""
        start_date, end_date, days, error = self._parse("start_date=2025-01-01&end_date=2025-01-31")
        assert error is None
        assert start_date == "2025-01-01"
        assert end_date == "2025-01-31"

    # --- priority rules ---

    def test_explicit_start_date_takes_priority_over_days(self):
        """Explicit start_date wins; days must not override it."""
        start_date, end_date, days, error = self._parse(
            "start_date=2026-01-10&end_date=2026-01-20&days=3"
        )
        assert error is None
        assert start_date == "2026-01-10"  # not end_date - 3 days
        assert end_date == "2026-01-20"
        assert days == 3

    def test_days_validated_even_when_unused(self):
        """days is validated whenever provided, even with explicit dates."""
        start_date, end_date, days, error = self._parse(
            "start_date=2026-01-10&end_date=2026-01-20&days=0"
        )
        assert error is not None
        assert error["parameter"] == "days"

    # --- invalid days -> 400 error dict ---

    def test_days_zero_rejected(self):
        """Test that days=0 returns a 400 error instead of clamping to 1."""
        start_date, end_date, days, error = self._parse("days=0")
        assert error is not None
        assert start_date is None
        assert end_date is None
        assert days is None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "days"
        assert error["received"] == 0
        assert "1" in error["message"] and "365" in error["message"]

    def test_days_negative_rejected(self):
        """Test that negative days returns a 400 error instead of clamping to 1."""
        start_date, end_date, days, error = self._parse("days=-5")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "days"
        # Rejected by the ASCII-digit regex before int() runs, so the raw
        # string is echoed back.
        assert error["received"] == "-5"

    def test_days_over_365_rejected(self):
        """Test that days > 365 returns a 400 error instead of clamping to 365."""
        start_date, end_date, days, error = self._parse("days=500")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "days"
        assert error["received"] == 500

    def test_days_366_rejected(self):
        """Test that days=366 (just above the boundary) is rejected."""
        start_date, end_date, days, error = self._parse("days=366")
        assert error is not None
        assert error["parameter"] == "days"
        assert error["received"] == 366

    def test_days_non_integer_rejected(self):
        """Test that non-integer days returns a 400 error."""
        start_date, end_date, days, error = self._parse("days=abc")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "days"
        assert error["received"] == "abc"

    def test_days_float_rejected(self):
        """Test that float days returns a 400 error."""
        start_date, end_date, days, error = self._parse("days=7.5")
        assert error is not None
        assert error["parameter"] == "days"
        assert error["received"] == "7.5"

    def test_days_huge_value_rejected(self):
        """Test that a huge days value returns a 400 error."""
        start_date, end_date, days, error = self._parse("days=999999999")
        assert error is not None
        assert error["parameter"] == "days"

    @pytest.mark.parametrize(
        "raw",
        ["+7", " 7", "1_0", "7%EF%BC%97"],
    )
    def test_days_lenient_int_forms_rejected(self, raw):
        """int() accepts "+7", " 7", "1_0" and full-width digits ("７" is
        percent-encoded here); none of them are legitimate query params, so
        the raw-string regex rejects them before int() ever runs."""
        start_date, end_date, days, error = self._parse(f"days={raw}")
        assert error is not None
        assert error["parameter"] == "days"
        assert error["received"] != 7

    # --- invalid dates -> 400 error dict ---

    def test_invalid_start_date_text_rejected(self):
        """Test that non-YYYY-MM-DD start_date returns a 400 error."""
        start_date, end_date, days, error = self._parse("start_date=not-a-date&end_date=2026-01-31")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "start_date"
        assert error["received"] == "not-a-date"

    def test_invalid_end_date_text_rejected(self):
        """Test that non-YYYY-MM-DD end_date returns a 400 error."""
        start_date, end_date, days, error = self._parse("end_date=20260131")
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "end_date"
        assert error["received"] == "20260131"

    def test_invalid_end_date_with_days_rejected(self):
        """Invalid end_date must 400, not blow up in strptime (used to 500)."""
        start_date, end_date, days, error = self._parse("end_date=oops&days=7")
        assert error is not None
        assert error["parameter"] == "end_date"

    def test_impossible_calendar_date_rejected(self):
        """Test that a syntactically-plausible but invalid date is rejected."""
        start_date, end_date, days, error = self._parse("start_date=2026-01-01&end_date=2026-02-30")
        assert error is not None
        assert error["parameter"] == "end_date"

    def test_slash_format_date_rejected(self):
        """Test that YYYY/MM/DD is rejected; only strict YYYY-MM-DD is valid."""
        start_date, end_date, days, error = self._parse("start_date=2026/01/01&end_date=2026-01-31")
        assert error is not None
        assert error["parameter"] == "start_date"

    @pytest.mark.parametrize(
        "query",
        [
            "start_date=2026-9-29&end_date=2026-10-05",
            "start_date=2026-09-1&end_date=2026-10-05",
            "start_date=2026-09-29&end_date=2026-9-30",
        ],
    )
    def test_non_padded_date_rejected(self, query):
        """Non-zero-padded dates pass strptime but must be rejected.

        The raw string flows verbatim into the SQL string comparison
        (date >= ? AND date <= ?), where "2026-9-29" sorts after
        "2026-10-31" and silently returns wrong/empty results -- exactly the
        failure class issue #3253 exists to eliminate.
        """
        start_date, end_date, days, error = self._parse(query)
        assert error is not None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] in ("start_date", "end_date")
        assert error["valid_range"] == "YYYY-MM-DD"

    @pytest.mark.parametrize(
        "query,param",
        [
            ("start_date=&end_date=2026-01-31", "start_date"),
            ("end_date=", "end_date"),
            ("days=&start_date=2026-01-01&end_date=2026-01-31", "days"),
        ],
    )
    def test_empty_string_params_rejected(self, query, param):
        """An explicitly empty param counts as provided-and-invalid.

        The old code treated empty start_date as absent via truthiness; the
        strict reading (issue #3253: validate whenever provided) makes it a
        400 instead.
        """
        start_date, end_date, days, error = self._parse(query)
        assert error is not None
        assert error["parameter"] == param

    def test_multi_invalid_reports_days_first(self):
        """days is validated before the dates, so its error wins."""
        start_date, end_date, days, error = self._parse("days=0&end_date=bad&start_date=also-bad")
        assert error is not None
        assert error["parameter"] == "days"

    def test_multi_invalid_reports_end_date_before_start_date(self):
        """end_date is validated before start_date, so its error wins."""
        start_date, end_date, days, error = self._parse("end_date=bad&start_date=also-bad")
        assert error is not None
        assert error["parameter"] == "end_date"

    # --- reversed range -> 400 error dict ---

    def test_start_date_after_end_date_rejected(self):
        """Test that start_date > end_date returns a 400 error, no silent swap."""
        start_date, end_date, days, error = self._parse("start_date=2026-01-31&end_date=2026-01-01")
        assert error is not None
        assert start_date is None
        assert end_date is None
        assert error["error"] == "invalid_parameter"
        assert error["parameter"] == "start_date"
        assert error["received"] == "2026-01-31"
        assert "end_date" in error["message"]

    def test_start_equal_end_date_valid(self):
        """Test that start_date == end_date is a valid single-day range."""
        start_date, end_date, days, error = self._parse("start_date=2026-01-31&end_date=2026-01-31")
        assert error is None
        assert start_date == end_date == "2026-01-31"


def _mock_report():
    """Build a UsageReport-like mock for generate_report return values."""
    report = MagicMock()
    report.to_dict.return_value = {"total_tokens": 100, "total_requests": 5}
    report.total_tokens = 100
    report.total_input_tokens = 60
    report.total_output_tokens = 40
    report.total_requests = 5
    report.unique_tools = 2
    report.unique_hosts = 1
    report.daily_average_tokens = 20.0
    report.peak_day = "2026-01-15"
    report.peak_tokens = 100
    report.breakdown_by_tool = {}
    report.breakdown_by_host = {}
    report.trends = []
    report.anomalies = []
    return report


class TestAnalyticsEndpointsParamValidation:
    """Endpoint-level tests: the three analytics endpoints share the same
    strict validation (issue #3253) and return 400 with a unified error body
    without executing the analytics query, export, or audit log."""

    ENDPOINTS = (
        "/api/analytics/report",
        "/api/analytics/efficiency",
        "/api/analytics/export",
    )

    @pytest.fixture
    def endpoint_deps(self):
        """Patch the module-level analytics/audit instances used by the routes."""
        report = _mock_report()
        with patch("app.routes.analytics.usage_analytics") as mock_usage:
            with patch("app.routes.analytics.audit_logger") as mock_audit:
                mock_usage.generate_report.return_value = report
                mock_usage.get_efficiency_metrics.return_value = {"score": 1.0}
                yield mock_usage, mock_audit

    def test_reversed_dates_rejected_on_all_endpoints(self, admin_client, endpoint_deps):
        """Reversed date range -> 400 on all three endpoints, same error body."""
        mock_usage, mock_audit = endpoint_deps
        bodies = []
        for endpoint in self.ENDPOINTS:
            response = admin_client.get(
                endpoint, query_string={"start_date": "2026-01-31", "end_date": "2026-01-01"}
            )
            assert response.status_code == 400, endpoint
            bodies.append(response.get_json())
        assert bodies[0] == bodies[1] == bodies[2]
        assert bodies[0]["error"] == "invalid_parameter"
        assert bodies[0]["parameter"] == "start_date"
        mock_usage.generate_report.assert_not_called()
        mock_usage.get_efficiency_metrics.assert_not_called()
        mock_audit.log_action.assert_not_called()

    def test_invalid_date_text_rejected_on_all_endpoints(self, admin_client, endpoint_deps):
        """Invalid date text -> 400 on all three endpoints."""
        mock_usage, mock_audit = endpoint_deps
        for endpoint in self.ENDPOINTS:
            response = admin_client.get(endpoint, query_string={"end_date": "not-a-date"})
            assert response.status_code == 400, endpoint
            body = response.get_json()
            assert body["error"] == "invalid_parameter"
            assert body["parameter"] == "end_date"
        mock_usage.generate_report.assert_not_called()
        mock_usage.get_efficiency_metrics.assert_not_called()
        mock_audit.log_action.assert_not_called()

    @pytest.mark.parametrize("days", [0, -1, "abc", 366])
    def test_invalid_days_rejected_on_all_endpoints(self, admin_client, endpoint_deps, days):
        """days of 0/-1/abc/366 -> 400 on all three endpoints."""
        mock_usage, mock_audit = endpoint_deps
        for endpoint in self.ENDPOINTS:
            response = admin_client.get(endpoint, query_string={"days": str(days)})
            assert response.status_code == 400, (endpoint, days)
            body = response.get_json()
            assert body["error"] == "invalid_parameter"
            assert body["parameter"] == "days"
        mock_usage.generate_report.assert_not_called()
        mock_usage.get_efficiency_metrics.assert_not_called()
        mock_audit.log_action.assert_not_called()

    @pytest.mark.parametrize("days", [1, 365])
    def test_boundary_days_accepted_on_all_endpoints(self, admin_client, endpoint_deps, days):
        """Boundary days=1 and days=365 succeed on all three endpoints."""
        mock_usage, mock_audit = endpoint_deps
        for endpoint in self.ENDPOINTS:
            response = admin_client.get(endpoint, query_string={"days": days})
            assert response.status_code == 200, (endpoint, days)
        assert mock_usage.generate_report.call_count == 2  # report + export
        assert mock_usage.get_efficiency_metrics.call_count == 1

    def test_report_runs_query_and_audit_for_valid_range(self, admin_client, endpoint_deps):
        """Sanity check: a valid range still executes query and audit log."""
        mock_usage, mock_audit = endpoint_deps
        response = admin_client.get(
            "/api/analytics/report",
            query_string={"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        assert response.status_code == 200
        assert response.get_json() == {"total_tokens": 100, "total_requests": 5}
        mock_usage.generate_report.assert_called_once()
        mock_audit.log_action.assert_called_once()

    def test_error_format_matches_forecast_days_structure(self, admin_client, endpoint_deps):
        """The 400 body uses the same keys as validate_forecast_days errors."""
        response = admin_client.get("/api/analytics/report", query_string={"days": "abc"})
        assert response.status_code == 400
        body = response.get_json()
        assert set(body.keys()) == {"error", "message", "parameter", "received", "valid_range"}
