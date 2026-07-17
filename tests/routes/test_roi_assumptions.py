#!/usr/bin/env python3
"""Route tests for ROI assumption overrides and validation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = str(Path(__file__).resolve().parent.parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.modules.analytics.roi_calculator import ROIAssumptions, ROIMetrics

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


def _metrics(assumptions: ROIAssumptions) -> ROIMetrics:
    return ROIMetrics(
        period="2026-01-01 to 2026-01-31",
        start_date="2026-01-01",
        end_date="2026-01-31",
        total_cost=12.5,
        tokens_used=1000,
        requests_made=20,
        estimated_hours_saved=2.0,
        estimated_savings=120.0,
        productivity_gain=900.0,
        roi_percentage=860.0,
        assumptions=assumptions,
    )


class TestRoiAssumptionRoutes:
    def test_get_roi_passes_overrides_to_calculator(self, client):
        fake_calc = MagicMock()
        fake_calc.calculate_roi.return_value = _metrics(
            ROIAssumptions(
                hourly_labor_cost=80.0,
                productivity_multiplier=6.0,
                avg_time_saved_per_request=12.0,
                currency="CNY",
            )
        )

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=fake_calc) as calc_cls:
                resp = client.get(
                    "/api/roi",
                    headers={"Authorization": "Bearer t"},
                    query_string={
                        "hourly_labor_cost": "80",
                        "productivity_multiplier": "6",
                        "avg_time_saved_per_request": "12",
                        "currency": "cny",
                    },
                )

        assert resp.status_code == 200
        assumptions = calc_cls.call_args.kwargs["assumptions"]
        assert assumptions.hourly_labor_cost == 80.0
        assert assumptions.productivity_multiplier == 6.0
        assert assumptions.avg_time_saved_per_request == 12.0
        assert assumptions.currency == "CNY"

    def test_invalid_roi_assumption_returns_400(self, client):
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={"hourly_labor_cost": "-5"},
            )

        assert resp.status_code == 400
        assert "hourly_labor_cost" in resp.get_json()["error"]
