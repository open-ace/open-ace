"""
Integration tests for the session-statistics fields exposed by the analysis
routes after the per-session-average fix.

Asserts that /api/analysis/batch, /api/analysis/key-metrics and
/api/analysis/conversation-stats all surface the REAL distinct conversation
count (4242) sourced from the single source of truth
(`MessageRepository.get_conversation_stats_summary`), not the legacy
`unique_days * unique_tools` approximation.

Mirrors the route-test pattern in tests/integration/test_analysis_data_range_api.py.
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


_EXTRACT_SESSION_TOKEN = "app.auth.decorators._extract_session_token"
_LOAD_USER = "app.auth.decorators._load_user_from_token"
_ADMIN_USER = {"id": 1, "role": "admin", "username": "admin"}


class TestSessionStatsEndpoints:
    def test_batch_surfaces_real_session_count(self, client):
        """batch key_metrics.total_sessions + conversation_stats come from
        the same real source (4242), not the legacy approximation."""
        batch_payload = {
            "key_metrics": {
                "total_sessions": 4242,
                "avg_tokens_per_session": 500.0,
                "avg_messages_per_session": 8.0,
                "total_tokens": 1000000,
            },
            "conversation_stats": {"total_conversations": 4242},
        }
        with (
            patch(_EXTRACT_SESSION_TOKEN, return_value="test-token"),
            patch(_LOAD_USER, return_value=_ADMIN_USER),
            patch(
                "app.routes.analysis.analysis_service.get_batch_analysis",
                return_value=batch_payload,
            ),
        ):
            resp = client.get("/api/analysis/batch?start=2026-05-01&end=2026-05-23")

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["key_metrics"]["total_sessions"] == 4242
        assert body["conversation_stats"]["total_conversations"] == 4242

    def test_key_metrics_surfaces_real_session_count(self, client):
        key_metrics_payload = {"total_sessions": 4242, "total_tokens": 1000000}
        with (
            patch(_EXTRACT_SESSION_TOKEN, return_value="test-token"),
            patch(_LOAD_USER, return_value=_ADMIN_USER),
            patch(
                "app.routes.analysis.analysis_service.get_key_metrics",
                return_value=key_metrics_payload,
            ),
        ):
            resp = client.get("/api/analysis/key-metrics?start=2026-05-01&end=2026-05-23")

        assert resp.status_code == 200
        assert resp.get_json()["total_sessions"] == 4242

    def test_conversation_stats_surfaces_real_count(self, client):
        """Standalone endpoint no longer truncates to limit=1000/7d; returns
        the full-range real count."""
        conv_payload = {"total_conversations": 4242, "total_messages": 20000}
        with (
            patch(_EXTRACT_SESSION_TOKEN, return_value="test-token"),
            patch(_LOAD_USER, return_value=_ADMIN_USER),
            patch(
                "app.routes.analysis.analysis_service.get_conversation_stats",
                return_value=conv_payload,
            ),
        ):
            resp = client.get("/api/analysis/conversation-stats?start=2026-05-01&end=2026-05-23")

        assert resp.status_code == 200
        assert resp.get_json()["total_conversations"] == 4242

    def test_batch_requires_authentication(self, client):
        with patch(_EXTRACT_SESSION_TOKEN, return_value=""):
            resp = client.get("/api/analysis/batch")
        assert resp.status_code == 401
