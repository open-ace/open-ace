#!/usr/bin/env python3
"""Integration tests for ROI date range validation.

Issue #2738: Verify HTTP 400 responses for invalid date inputs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

MOCK_ADMIN_SESSION = {
    "user_id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
}


@pytest.fixture
def client():
    from flask import Flask

    from app.routes.roi import roi_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(roi_bp, url_prefix="/api")
    return app.test_client()


class TestRoiDateValidation:
    """Integration tests for ROI date range validation."""

    def test_only_start_date_returns_400(self, client):
        """Only providing start_date should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"start_date": "2026-01-01"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "error_code" in data
        assert data["error_code"] == "incomplete_date_range"

    def test_only_end_date_returns_400(self, client):
        """Only providing end_date should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"end_date": "2026-01-31"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "incomplete_date_range"

    def test_invalid_date_format_returns_400(self, client):
        """Invalid date format should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"start_date": "2026/01/01", "end_date": "2026-01-31"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_date_format"

    def test_start_after_end_returns_400(self, client):
        """Start date after end date should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"start_date": "2026-01-31", "end_date": "2026-01-01"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_date_order"

    def test_invalid_calendar_date_returns_400(self, client):
        """Invalid calendar date should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"start_date": "2026-02-30", "end_date": "2026-03-01"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_date_format"

    def test_months_zero_returns_400(self, client):
        """months=0 should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi/trend",
                headers={"Authorization": "Bearer t"},
                query_string={"months": "0"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_time_window"

    def test_months_negative_returns_400(self, client):
        """Negative months should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi/trend",
                headers={"Authorization": "Bearer t"},
                query_string={"months": "-1"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_time_window"

    def test_days_zero_returns_400(self, client):
        """days=0 should return HTTP 400 with error_code."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/optimization/suggestions",
                headers={"Authorization": "Bearer t"},
                query_string={"days": "0"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "invalid_time_window"

    def test_valid_request_still_works(self, client):
        """Valid date range should still return 200 (mock test)."""
        fake_calc = MagicMock()
        fake_calc.calculate_roi.return_value = MagicMock(
            to_dict=lambda: {
                "period": "2026-01-01 to 2026-01-31",
                "total_cost": 10.0,
            }
        )

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=fake_calc):
                resp = client.get(
                    "/api/roi",
                    headers={"Authorization": "Bearer t"},
                    query_string={"start_date": "2026-01-01", "end_date": "2026-01-31"},
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_both_dates_missing_applies_default(self, client):
        """Both dates missing should apply default and return 200."""
        fake_calc = MagicMock()
        fake_calc.calculate_roi.return_value = MagicMock(
            to_dict=lambda: {
                "period": "2026-01-01 to 2026-01-31",
                "total_cost": 10.0,
            }
        )

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=fake_calc):
                resp = client.get(
                    "/api/roi",
                    headers={"Authorization": "Bearer t"},
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True

    def test_error_message_is_user_friendly(self, client):
        """Error message should not contain SQL or stack traces."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"start_date": "2026-01-31", "end_date": "2026-01-01"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        error_msg = data.get("error", "")
        # Should not contain technical details
        assert "SQL" not in error_msg.upper()
        assert "Traceback" not in error_msg
        assert "Exception" not in error_msg


class TestRoiAssumptionsStillWork:
    """Verify ROI assumptions validation still works after date validation changes."""

    def test_invalid_assumption_returns_400(self, client):
        """Invalid assumption override should still return 400."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"hourly_labor_cost": "-5"},
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        # This should not have error_code since it's existing validation
        # Just check that it returns 400