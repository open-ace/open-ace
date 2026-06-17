"""
Integration test for the GET /api/analysis/data-range endpoint.

Verifies the route wires the AnalysisService result through to the client,
including the null response when there is no data (consumed by the frontend
"All" quick-range fallback). Mirrors the route-test pattern in
tests/integration/test_data_retention_api.py.
"""

from unittest.mock import patch

import pytest


@pytest.fixture
def app():
    """Flask app with only the analysis blueprint mounted under /api."""
    from flask import Flask

    from app.routes.analysis import analysis_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(analysis_bp, url_prefix="/api")
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


# Auth helpers shared with the retention API tests.
_EXTRACT_TOKEN = "app.auth.decorators._extract_token"
_LOAD_USER = "app.auth.decorators._load_user_from_token"
_ADMIN_USER = {"id": 1, "role": "admin", "username": "admin"}


class TestDataRangeEndpoint:
    def test_returns_data_range(self, client):
        """A populated DB yields 200 with {min_date, max_date}."""
        payload = {"min_date": "2024-01-01", "max_date": "2026-06-17"}
        with (
            patch(_EXTRACT_TOKEN, return_value="test-token"),
            patch(_LOAD_USER, return_value=_ADMIN_USER),
            patch(
                "app.routes.analysis.analysis_service.get_data_range",
                return_value=payload,
            ),
        ):
            resp = client.get("/api/analysis/data-range")

        assert resp.status_code == 200
        assert resp.get_json() == payload

    def test_returns_null_when_no_data(self, client):
        """An empty DB yields 200 with a null body (frontend falls back)."""
        with (
            patch(_EXTRACT_TOKEN, return_value="test-token"),
            patch(_LOAD_USER, return_value=_ADMIN_USER),
            patch(
                "app.routes.analysis.analysis_service.get_data_range",
                return_value=None,
            ),
        ):
            resp = client.get("/api/analysis/data-range")

        assert resp.status_code == 200
        assert resp.get_json() is None

    def test_requires_authentication(self, client):
        """Missing token -> 401 (blueprint-level @auth_required)."""
        with patch(_EXTRACT_TOKEN, return_value=None):
            resp = client.get("/api/analysis/data-range")
        assert resp.status_code == 401
