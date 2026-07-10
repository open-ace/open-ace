#!/usr/bin/env python3
"""Tests for the feature flags API endpoint."""

from unittest.mock import patch

import pytest
from flask import Flask


@pytest.fixture
def ff_app():
    """Flask app with only the feature_flags blueprint."""
    from app.routes.feature_flags import feature_flags_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(feature_flags_bp)
    return app


class TestFeatureFlagsAPI:
    """Test feature flags API endpoint."""

    @patch("app.routes.feature_flags.is_model_gateway_enabled")
    @patch("app.routes.feature_flags.is_run_timeline_enabled")
    @patch("app.routes.feature_flags.is_policy_enabled")
    @patch("app.routes.feature_flags.is_autonomous_enabled")
    def test_get_feature_flags_returns_all_flags(
        self, mock_autonomous, mock_policy, mock_timeline, mock_gateway, ff_app
    ):
        """Test that GET returns all feature flags."""
        mock_gateway.return_value = False
        mock_timeline.return_value = True
        mock_policy.return_value = False
        mock_autonomous.return_value = False

        resp = ff_app.test_client().get("/api/feature-flags")
        assert resp.status_code == 200
        data = resp.get_json()

        assert "model_gateway" in data
        assert "run_timeline" in data
        assert "policy" in data
        assert "autonomous" in data

        assert data["model_gateway"] is False
        assert data["run_timeline"] is True
        assert data["policy"] is False
        assert data["autonomous"] is False

    @patch("app.routes.feature_flags.is_model_gateway_enabled")
    @patch("app.routes.feature_flags.is_run_timeline_enabled")
    @patch("app.routes.feature_flags.is_policy_enabled")
    @patch("app.routes.feature_flags.is_autonomous_enabled")
    def test_get_feature_flags_all_enabled(
        self, mock_autonomous, mock_policy, mock_timeline, mock_gateway, ff_app
    ):
        """Test that GET returns true when all features are enabled."""
        mock_gateway.return_value = True
        mock_timeline.return_value = True
        mock_policy.return_value = True
        mock_autonomous.return_value = True

        resp = ff_app.test_client().get("/api/feature-flags")
        assert resp.status_code == 200
        data = resp.get_json()

        assert data["model_gateway"] is True
        assert data["run_timeline"] is True
        assert data["policy"] is True
        assert data["autonomous"] is True

    @patch("app.routes.feature_flags.is_model_gateway_enabled")
    @patch("app.routes.feature_flags.is_run_timeline_enabled")
    @patch("app.routes.feature_flags.is_policy_enabled")
    @patch("app.routes.feature_flags.is_autonomous_enabled")
    def test_get_feature_flags_returns_500_on_error(
        self, mock_autonomous, mock_policy, mock_timeline, mock_gateway, ff_app
    ):
        """Test that GET returns 500 when flag functions raise."""
        mock_gateway.side_effect = Exception("Config read error")
        mock_timeline.return_value = False
        mock_policy.return_value = False
        mock_autonomous.return_value = False

        resp = ff_app.test_client().get("/api/feature-flags")
        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])