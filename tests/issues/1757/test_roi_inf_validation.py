#!/usr/bin/env python3
"""RED tests for ROI findings on PR #1757.

Covers:
- inf/Infinity must be rejected by positive-float validation (request + env)
- cost-breakdown / daily-costs must not parse ROI assumption overrides
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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


class TestRoiInfRejection:
    @pytest.mark.parametrize(
        "param",
        ["hourly_labor_cost", "productivity_multiplier", "avg_time_saved_per_request"],
    )
    def test_request_inf_rejected_with_400(self, client, param):
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={param: "inf"},
            )
        assert resp.status_code == 400
        assert param in resp.get_json()["error"]

    @pytest.mark.parametrize(
        "param",
        ["hourly_labor_cost", "productivity_multiplier", "avg_time_saved_per_request"],
    )
    def test_request_infinity_rejected_with_400(self, client, param):
        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            resp = client.get(
                "/api/roi",
                headers={"Authorization": "Bearer t"},
                query_string={param: "Infinity"},
            )
        assert resp.status_code == 400

    def test_env_inf_falls_back_to_default(self, monkeypatch):
        from app.modules.analytics.roi_calculator import ROIAssumptions

        monkeypatch.setenv("OPENACE_ROI_HOURLY_LABOR_COST", "inf")
        monkeypatch.delenv("OPENACE_ROI_PRODUCTIVITY_MULTIPLIER", raising=False)
        monkeypatch.delenv("OPENACE_ROI_AVG_TIME_SAVED_PER_REQUEST", raising=False)
        monkeypatch.delenv("OPENACE_ROI_CURRENCY", raising=False)

        assumptions = ROIAssumptions.from_env()
        # inf must NOT propagate; the default (50.0) is used instead.
        assert assumptions.hourly_labor_cost == 50.0


class TestCostRoutesDropAssumptions:
    """cost-breakdown and daily-costs must not accept assumption overrides."""

    @pytest.mark.parametrize(
        "path,method_name",
        [
            ("/api/roi/cost-breakdown", "get_cost_breakdown"),
            ("/api/roi/daily-costs", "get_daily_costs"),
        ],
    )
    def test_bad_assumption_override_does_not_400(self, client, path, method_name):
        """Previously these routes validated assumption params (returning 400 on
        bad input) even though the values were never used. They should now
        ignore override params entirely."""
        fake_calc = MagicMock()
        getattr(fake_calc, method_name).return_value = []

        with patch("app.auth.decorators._authenticate", return_value=(True, MOCK_ADMIN_SESSION)):
            with patch("app.routes.roi.ROICalculator", return_value=fake_calc):
                resp = client.get(
                    path,
                    headers={"Authorization": "Bearer t"},
                    query_string={"hourly_labor_cost": "-5"},
                )
        assert resp.status_code == 200
