"""Forecast API route contract regression tests for Issue #2826."""

from __future__ import annotations

import pytest
from flask import Flask

from app.routes.analytics import analytics_bp

pytestmark = [pytest.mark.regression, pytest.mark.issue(2826)]

CANONICAL_PATH = "/api/analytics/forecast"
LEGACY_PATH = "/api/analysis/forecast"


def _create_route_app() -> Flask:
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="issue-2826-route-contract")
    app.register_blueprint(analytics_bp, url_prefix="/api")
    return app


def test_forecast_paths_share_the_authenticated_handler():
    """The canonical path and compatibility alias must not drift."""
    app = _create_route_app()
    forecast_rules = {
        rule.rule: rule
        for rule in app.url_map.iter_rules()
        if rule.rule in {CANONICAL_PATH, LEGACY_PATH}
    }

    assert set(forecast_rules) == {CANONICAL_PATH, LEGACY_PATH}
    assert all("GET" in rule.methods for rule in forecast_rules.values())

    endpoints = {rule.endpoint for rule in forecast_rules.values()}
    views = {app.view_functions[rule.endpoint] for rule in forecast_rules.values()}
    assert endpoints == {"analytics.api_usage_forecast"}
    assert len(views) == 1


def test_forecast_paths_apply_the_same_authentication_gate():
    """Both registered paths must reject an unauthenticated request identically."""
    client = _create_route_app().test_client()

    canonical_response = client.get(f"{CANONICAL_PATH}?days=7")
    legacy_response = client.get(f"{LEGACY_PATH}?days=7")

    assert canonical_response.status_code == 401
    assert legacy_response.status_code == 401
    assert canonical_response.get_json() == legacy_response.get_json()
