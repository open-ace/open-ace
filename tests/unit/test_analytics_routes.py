"""Unit tests for analytics routes date range parsing."""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from app.routes.analytics import parse_date_range


class TestParseDateRange:
    """Test parse_date_range function."""

    def _create_app_with_request_context(self, query_string=""):
        """Create a Flask app with request context for testing."""
        app = Flask(__name__)
        app.config["TESTING"] = True

        @app.route("/test")
        def test_route():
            return parse_date_range()

        return app

    def test_default_values(self):
        """Test default date range when no parameters provided."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test"):
                start_date, end_date, days = parse_date_range()
                # Default: end_date=today, days=30
                assert days == 30
                assert end_date is not None
                assert start_date is not None

    def test_explicit_start_date_and_end_date(self):
        """Test explicit start_date and end_date parameters."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?start_date=2026-01-01&end_date=2026-01-31"):
                start_date, end_date, days = parse_date_range()
                assert start_date == "2026-01-01"
                assert end_date == "2026-01-31"
                assert days == 30  # days is returned but not used when start_date is explicit

    def test_only_start_date_uses_default_end_date(self):
        """Test that providing only start_date uses default end_date (today)."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?start_date=2026-01-01"):
                start_date, end_date, days = parse_date_range()
                assert start_date == "2026-01-01"
                # end_date defaults to today
                assert end_date is not None

    def test_only_end_date_uses_days_to_calculate_start(self):
        """Test that providing end_date + days calculates start_date from end_date."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?end_date=2026-01-31&days=7"):
                start_date, end_date, days = parse_date_range()
                # start_date should be end_date - 7 days = 2026-01-24
                assert start_date == "2026-01-24"
                assert end_date == "2026-01-31"
                assert days == 7

    def test_days_7(self):
        """Test 7-day range."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?days=7"):
                start_date, end_date, days = parse_date_range()
                assert days == 7

    def test_days_90(self):
        """Test 90-day range."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?days=90"):
                start_date, end_date, days = parse_date_range()
                assert days == 90

    def test_days_zero_clamped_to_one(self):
        """Test that days=0 is clamped to 1."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?days=0"):
                start_date, end_date, days = parse_date_range()
                assert days == 1

    def test_days_negative_clamped_to_one(self):
        """Test that negative days is clamped to 1."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?days=-5"):
                start_date, end_date, days = parse_date_range()
                assert days == 1

    def test_days_over_365_clamped(self):
        """Test that days > 365 is clamped to 365."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?days=500"):
                start_date, end_date, days = parse_date_range()
                assert days == 365

    def test_start_date_greater_than_end_date_swapped(self):
        """Test that start_date > end_date results in swap."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?start_date=2026-01-31&end_date=2026-01-01"):
                start_date, end_date, days = parse_date_range()
                # Should be swapped
                assert start_date == "2026-01-01"
                assert end_date == "2026-01-31"

    def test_historical_end_date_with_days(self):
        """Test historical end_date with days calculates correct start_date."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?end_date=2025-12-01&days=30"):
                start_date, end_date, days = parse_date_range()
                # start_date should be 2025-12-01 - 30 days = 2025-11-01
                assert start_date == "2025-11-01"
                assert end_date == "2025-12-01"
                assert days == 30

    def test_historical_range_with_explicit_dates(self):
        """Test explicit historical date range."""
        app = self._create_app_with_request_context()
        with app.test_client():
            with app.test_request_context("/test?start_date=2025-01-01&end_date=2025-01-31"):
                start_date, end_date, days = parse_date_range()
                assert start_date == "2025-01-01"
                assert end_date == "2025-01-31"
