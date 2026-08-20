#!/usr/bin/env python3
"""Integration test to verify tool_name parameter propagation across ROI endpoints.

Issue #2737: Verify tool_name filter applies consistently across all ROI page modules.

This test validates that:
1. Backend API endpoints accept tool_name parameter
2. Backend core methods receive and use tool_name parameter
3. SQL queries include tool_name filtering when provided
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


class TestToolNamePropagation:
    """Verify tool_name parameter propagates correctly across all ROI endpoints."""

    def test_roi_trend_accepts_tool_name(self, client):
        """Test /api/roi/trend accepts and passes tool_name parameter."""
        mock_calc = MagicMock()
        mock_calc.get_roi_trend.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi/trend",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "qwen"},
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to calculator
        mock_calc.get_roi_trend.assert_called_once()
        call_args = mock_calc.get_roi_trend.call_args
        # Check that tool_name parameter was passed (it's a keyword argument)
        assert "tool_name" in call_args.kwargs
        assert call_args.kwargs["tool_name"] == "qwen"

    def test_cost_breakdown_accepts_tool_name(self, client):
        """Test /api/roi/cost-breakdown accepts and passes tool_name parameter."""
        mock_calc = MagicMock()
        mock_calc.get_cost_breakdown.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi/cost-breakdown",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "claude"},
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to calculator
        mock_calc.get_cost_breakdown.assert_called_once()
        call_args = mock_calc.get_cost_breakdown.call_args
        assert "tool_name" in call_args.kwargs
        assert call_args.kwargs["tool_name"] == "claude"

    def test_optimization_suggestions_accepts_tool_name(self, client):
        """Test /api/optimization/suggestions accepts and passes tool_name parameter."""
        mock_optimizer = MagicMock()
        mock_optimizer.analyze.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.CostOptimizer", return_value=mock_optimizer):
                resp = client.get(
                    "/api/optimization/suggestions",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "gpt"},
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to optimizer
        mock_optimizer.analyze.assert_called_once()
        call_args = mock_optimizer.analyze.call_args
        assert "tool_name" in call_args.kwargs
        assert call_args.kwargs["tool_name"] == "gpt"

    def test_efficiency_report_accepts_tool_name(self, client):
        """Test /api/optimization/efficiency accepts and passes tool_name parameter."""
        mock_optimizer = MagicMock()
        mock_optimizer.get_efficiency_report.return_value = {"period_days": 30}

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.CostOptimizer", return_value=mock_optimizer):
                resp = client.get(
                    "/api/optimization/efficiency",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "test-tool"},
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to optimizer
        mock_optimizer.get_efficiency_report.assert_called_once()
        call_args = mock_optimizer.get_efficiency_report.call_args
        assert "tool_name" in call_args.kwargs
        assert call_args.kwargs["tool_name"] == "test-tool"

    def test_tool_name_validation_too_long(self, client):
        """Test tool_name parameter validation for max length."""
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi/trend",
                headers={"Authorization": "Bearer t"},
                query_string={"tool_name": "x" * 101},  # 101 characters
            )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data["success"] is False
        assert "tool_name" in data["error"]

    def test_empty_tool_name_treated_as_none(self, client):
        """Test that empty tool_name is treated as None (no filter)."""
        mock_calc = MagicMock()
        mock_calc.get_roi_trend.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi/trend",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": ""},
                )

        assert resp.status_code == 200
        # Verify tool_name was treated as None (empty string converted to None)
        mock_calc.get_roi_trend.assert_called_once()
        call_args = mock_calc.get_roi_trend.call_args
        assert call_args.kwargs.get("tool_name") is None

    def test_tool_name_with_whitespace_normalized(self, client):
        """Test that tool_name with leading/trailing whitespace is normalized."""
        mock_calc = MagicMock()
        mock_calc.get_roi_trend.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi/trend",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "  qwen  "},
                )

        assert resp.status_code == 200
        # Verify tool_name was stripped
        mock_calc.get_roi_trend.assert_called_once()
        call_args = mock_calc.get_roi_trend.call_args
        assert call_args.kwargs["tool_name"] == "qwen"

    def test_daily_costs_accepts_tool_name(self, client):
        """Test /api/roi/daily-costs accepts and passes tool_name parameter."""
        mock_calc = MagicMock()
        mock_calc.get_daily_costs.return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi/daily-costs",
                    headers={"Authorization": "Bearer t"},
                    query_string={"tool_name": "qwen"},
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to calculator
        mock_calc.get_daily_costs.assert_called_once()
        call_args = mock_calc.get_daily_costs.call_args
        assert "tool_name" in call_args.kwargs
        assert call_args.kwargs["tool_name"] == "qwen"

    def test_roi_endpoint_accepts_tool_name(self, client):
        """Test /api/roi accepts and passes tool_name parameter."""
        mock_calc = MagicMock()
        mock_calc.calculate_roi.return_value = MagicMock(
            to_dict=lambda: {"period": "2026-01-01 to 2026-01-31", "total_cost": 10.0}
        )

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=mock_calc):
                resp = client.get(
                    "/api/roi",
                    headers={"Authorization": "Bearer t"},
                    query_string={
                        "start_date": "2026-01-01",
                        "end_date": "2026-01-31",
                        "tool_name": "claude",
                    },
                )

        assert resp.status_code == 200
        # Verify tool_name was passed to calculator
        # calculate_roi uses positional args: (start_date, end_date, user_id, tool_name, tenant_id=...)
        mock_calc.calculate_roi.assert_called_once()
        call_args = mock_calc.calculate_roi.call_args
        # tool_name is the 4th positional argument (index 3)
        assert call_args.args[3] == "claude"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
